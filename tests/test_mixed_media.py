"""Phase 1.10: mixed image, sequence, video plus additional audio and subtitles."""
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from PIL import Image
from ai_video_editor.project import ProjectManager, validate_storyboard, ValidationError
from ai_video_editor.assets import validate_assets
from ai_video_editor.planner import get_default_editing_plan
from ai_video_editor.timeline import build_timeline
from ai_video_editor.renderer import render_project
from ai_video_editor.audio import mux_project_audio
from ai_video_editor.qa import qa_project
from ai_video_editor.cli import main as cli_main
ROOT=Path(__file__).resolve().parents[1]

class MixedMediaTests(unittest.TestCase):
    def test_reject_video_path_traversal(self):
        storyboard={'schema_version':'1.1','project_id':'demo','title':'Test','voice_mode':'per_scene',
                    'scenes':[{'scene_id':'001','order':1,'type':'video_clip','video':'../../x.mp4','narration':''}]}
        with self.assertRaises(ValidationError): validate_storyboard(storyboard)

    def test_reject_unbounded_audio_volume(self):
        storyboard={'schema_version':'1.1','project_id':'demo','title':'Test','voice_mode':'per_scene',
                    'scenes':[], 'audio_tracks':[{'file':'assets/audio/song.wav','start_seconds':0,'volume':10}]}
        with self.assertRaises(ValidationError): validate_storyboard(storyboard)

    def test_mixed_three_sources_with_audio_and_subtitles(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory(prefix='phase110-') as temp:
            pm=ProjectManager(Path(temp)/'projects',ROOT/'config'/'default_project.json')
            project=pm.create_project('demo','Mixed')
            config=project.config
            config['planning']['require_plan_approval']=False
            config['output'].update(width=160,height=90,encoder_preset='ultrafast')
            config['motion']['supersample_factor']=1
            config['transitions'].update(intro='none',outro='none')
            config['timing'].update(pad_head_seconds=0,pad_tail_seconds=0)
            config['subtitles']['mode']='both'
            board=project.storyboard
            board['scenes']=[
                {'scene_id':'001','order':1,'narration':'Welcome','image':'assets/images/scene_001.png','audio':'assets/audio/voice.wav'},
                {'scene_id':'002','order':2,'type':'image_sequence','narration':'','sequence':{
                    'frames_dir':'assets/sequences/anim','frame_pattern':'frame_%04d.png','frame_count':30,'fps':'30/1'}},
                {'scene_id':'003','order':3,'type':'video_clip','narration':'','video':'assets/videos/clip.mp4'}]
            board['audio_tracks']=[{'file':'assets/audio/background.wav','start_seconds':0.4,'volume':0.1}]
            board['subtitles']=[{'start_seconds':0,'end_seconds':0.7,'text':'Hello world!'},
                                {'start_seconds':1.2,'end_seconds':1.8,'text':'Animation'}]
            project=pm.save_project('demo',config=config,storyboard=board)
            folder=project.folder
            Image.new('RGB',(160,90),'blue').save(folder/'assets/images/scene_001.png')
            with wave.open(str(folder/'assets/audio/voice.wav'),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000);out.writeframes(b'\0\0'*48000)
            with wave.open(str(folder/'assets/audio/background.wav'),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000);out.writeframes(b'\0\0'*48000)
            seq=folder/'assets/sequences/anim';seq.mkdir(parents=True)
            for n in range(1,31):
                Image.new('RGB',(160,90),(n*7%256,40,30)).save(seq/f'frame_{n:04d}.png')
            clips=folder/'assets/videos';clips.mkdir(parents=True,exist_ok=True)
            subprocess.run(['ffmpeg','-loglevel','error','-y','-f','lavfi','-i','color=c=green:s=160x90:r=24:d=2',
                            '-c:v','libx264','-pix_fmt','yuv420p',str(clips/'clip.mp4')],check=True)
            assets=validate_assets(project)
            self.assertEqual(assets['status'],'PASS',assets['scene_results'])
            plan=get_default_editing_plan(project,style='documentary',seed=123)
            self.assertEqual(plan['scenes'][2]['motion'],'static')
            timeline=build_timeline(project,asset_report=assets,editing_plan=plan)
            self.assertEqual(len(timeline['video_track']),3)
            self.assertEqual(timeline['video_track'][2]['duration_frames'],60)
            rendered=render_project(project)
            self.assertEqual(rendered['video']['frames'],timeline['total_frames'])
            mux=mux_project_audio(project)
            self.assertEqual(mux['final_qa']['video_frames'],timeline['total_frames'])
            self.assertEqual(mux['additional_audio_tracks'],1)
            self.assertTrue((folder/'output/subtitles.srt').exists())
            qa=qa_project(project)
            self.assertEqual(qa['status'],'PASS',qa['checks'])
            cached=render_project(project)
            self.assertEqual(cached['cached_scenes'],3)
            # The new one-command pipeline must complete on the same mixed project.
            result = cli_main(['--projects-dir',str(pm.projects_dir),
                               '--default-config',str(ROOT/'config/default_project.json'),
                               'compose','demo'])
            self.assertEqual(result,0)

if __name__=='__main__':unittest.main()
