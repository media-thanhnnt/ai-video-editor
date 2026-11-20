"""Regression tests for issues reported after Phase 1.10.1 acceptance."""
import shutil
import subprocess
import tempfile
import unittest
import wave
from fractions import Fraction
from pathlib import Path

from PIL import Image
from ai_video_editor.assets import get_video_info, validate_assets
from ai_video_editor.project import ProjectManager
from ai_video_editor.planner import get_default_editing_plan
from ai_video_editor.timeline import build_timeline
from ai_video_editor.renderer import render_project

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str,args)], check=True)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class Regressions(unittest.TestCase):
    def test_video_duration_ignores_longer_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            for audio_length in (2.03, 2.5):
                clip = Path(tmp) / f'clip_{audio_length}.mp4'
                run('-f','lavfi','-i','color=c=red:s=160x90:r=30:d=2',
                    '-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={audio_length}',
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',clip)
                info = get_video_info(clip, fps=Fraction(30, 1))
                self.assertEqual(info['target_frames'], 60, info)
                self.assertTrue(info['has_audio'])
                pm=ProjectManager(Path(tmp)/f'projects_{audio_length}', ROOT/'config/default_project.json')
                project=pm.create_project('demo','Video with extra audio tail')
                project.config['planning']['require_plan_approval']=False
                project.config['output'].update(width=160,height=90,encoder_preset='ultrafast')
                project.config['transitions'].update(intro='none',outro='none')
                project.storyboard['scenes']=[{'scene_id':'001','order':1,'type':'video_clip',
                                               'video':'assets/videos/clip.mp4','narration':''}]
                project=pm.save_project('demo',config=project.config,storyboard=project.storyboard)
                shutil.copyfile(clip,project.folder/'assets/videos/clip.mp4')
                assets=validate_assets(project)
                self.assertEqual(assets['status'],'PASS')
                plan=get_default_editing_plan(project)
                timeline=build_timeline(project,asset_report=assets,editing_plan=plan)
                self.assertEqual(timeline['total_frames'],60)
                result=render_project(project)
                self.assertEqual(result['video']['frames'],60)

    def test_sequence_with_voice_does_not_mutate_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProjectManager(Path(tmp)/'projects', ROOT/'config/default_project.json')
            project = pm.create_project('demo','Sequence voice')
            scene = {'scene_id':'001','order':1,'type':'image_sequence','narration':'hi',
                     'audio':'assets/audio/voice.wav',
                     'sequence': {'frames_dir':'assets/sequences/seq','frame_pattern':'frame_%04d.png',
                                  'frame_count':30,'fps':'30/1'}}
            project = pm.save_project('demo', storyboard={**project.storyboard,'scenes':[scene]})
            directory = project.folder/'assets/sequences/seq'
            directory.mkdir(parents=True)
            for n in range(1,31):
                Image.new('RGB',(160,90),'red').save(directory/f'frame_{n:04d}.png')
            with wave.open(str(project.folder/'assets/audio/voice.wav'),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(48000)
                audio.writeframes(b'\x00\x00' * 12000)
            report=validate_assets(project)
            self.assertEqual(report['status'],'PASS', report['scene_results'])
            self.assertEqual(report['scene_results'][0]['frame_count'],30)

    def test_silent_short_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm=ProjectManager(Path(tmp)/'projects',ROOT/'config/default_project.json')
            project=pm.create_project('demo','Short')
            project.config['transitions'].update(intro='none',outro='none')
            project.storyboard['scenes']=[{'scene_id':'001','order':1,'type':'image_sequence','narration':'',
                 'sequence':{'frames_dir':'assets/sequences/seq','frame_pattern':'frame_%04d.png',
                              'frame_count':10,'fps':'30/1'}}]
            project=pm.save_project('demo',config=project.config,storyboard=project.storyboard)
            directory=project.folder/'assets/sequences/seq'
            directory.mkdir(parents=True)
            for n in range(1,11): Image.new('RGB',(160,90),'red').save(directory/f'frame_{n:04d}.png')
            report=validate_assets(project)
            plan=get_default_editing_plan(project)
            timeline=build_timeline(project,asset_report=report,editing_plan=plan)
            self.assertEqual(timeline['total_frames'],10)
            self.assertEqual(timeline['video_track'][0]['head_padding_frames'],0)

    def test_bt709_single_colour_fidelity(self):
        # Validate actual decoded RGB, not just BT.709 metadata tags.
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            pm=ProjectManager(Path(tmp)/'projects',ROOT/'config/default_project.json')
            project=pm.create_project('demo','Color')
            project.config['planning']['require_plan_approval']=False
            project.config['output'].update(width=160,height=90,encoder_preset='ultrafast')
            project.config['motion']['supersample_factor']=1
            project.config['transitions'].update(intro='none',outro='none')
            project.config['timing'].update(pad_head_seconds=0,pad_tail_seconds=0)
            project.storyboard['scenes']=[{'scene_id':'001','order':1,'image':'assets/images/scene_001.png',
                                             'narration':'test','audio':'assets/audio/voice.wav','type':'image'}]
            project=pm.save_project('demo',config=project.config,storyboard=project.storyboard)
            Image.new('RGB',(160,90),(220,30,30)).save(project.folder/'assets/images/scene_001.png')
            with wave.open(str(project.folder/'assets/audio/voice.wav'),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(48000)
                audio.writeframes(b'\x00\x00' * 48000)
            report=validate_assets(project)
            plan=get_default_editing_plan(project)
            build_timeline(project,asset_report=report,editing_plan=plan)
            video=render_project(project)
            pixels=subprocess.run(['ffmpeg','-loglevel','error','-i',video['output'],
                '-vf','scale=in_color_matrix=bt709:in_range=tv,format=rgb24',
                '-frames:v','1','-f','rawvideo','pipe:1'],capture_output=True,check=True).stdout
            data=np.frombuffer(pixels,dtype=np.uint8).reshape(-1,3)
            measured=np.median(data,axis=0)
            self.assertTrue(np.all(abs(measured-np.array([220,30,30]))<=3),measured)
