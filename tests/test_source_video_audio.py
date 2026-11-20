"""Phase 1.10.1: audio inside video clips aligned to exact visual overlap."""
import json
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np

from ai_video_editor.project import ProjectManager, validate_storyboard, ValidationError
from ai_video_editor.assets import validate_assets
from ai_video_editor.planner import get_default_editing_plan
from ai_video_editor.timeline import build_timeline
from ai_video_editor.renderer import render_project
from ai_video_editor.audio import mux_project_audio

ROOT = Path(__file__).resolve().parents[1]


class SourceAudioTests(unittest.TestCase):
    def test_reject_invalid_source_audio(self):
        base = {'schema_version':'1.1', 'project_id':'demo', 'title':'Demo', 'voice_mode':'per_scene',
                'scenes':[{'scene_id':'001','order':1,'type':'video_clip','video':'assets/videos/test.mp4','narration':''}]}
        for option in ({'enabled':'yes'}, {'volume':3}, {'volume':-0.1}, {'foo':1}):
            import copy
            data = copy.deepcopy(base)
            data['scenes'][0]['source_audio'] = option
            with self.assertRaises(ValidationError):
                validate_storyboard(data)

    def test_two_video_audio_dissolve_and_no_audio_fallback(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory(prefix='phase111-') as tmp:
            pm = ProjectManager(Path(tmp)/'projects', ROOT/'config/default_project.json')
            project = pm.create_project('demo', 'Embedded Audio')
            project.config['planning']['require_plan_approval']=False
            config = project.config
            config['output'].update(width=160, height=90, encoder_preset='ultrafast')
            config['motion']['supersample_factor'] = 1
            config['transitions'].update(intro='none', outro='none')
            config['timing'].update(pad_head_seconds=0, pad_tail_seconds=0)
            board = project.storyboard
            board['scenes'] = [
                {'scene_id':'001','order':1,'type':'video_clip','video':'assets/videos/one.mp4','narration':'',
                 'source_audio':{'enabled':True,'volume':1.0}},
                {'scene_id':'002','order':2,'type':'video_clip','video':'assets/videos/two.mp4','narration':'',
                 'source_audio':{'enabled':True,'volume':1.0}},
            ]
            project = pm.save_project('demo', config=config, storyboard=board)
            video_dir = project.folder/'assets/videos'
            for filename, freq, colour in [('one.mp4',440,'blue'),('two.mp4',880,'red')]:
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
                                '-f','lavfi','-i',f'color=c={colour}:s=160x90:r=24:d=2',
                                '-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=48000:duration=2',
                                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
                                '-shortest',str(video_dir/filename)],check=True)
            assets = validate_assets(project)
            self.assertEqual(assets['status'],'PASS',assets['scene_results'])
            self.assertTrue(assets['file_results']['assets/videos/one.mp4']['info']['has_audio'])
            plan = get_default_editing_plan(project, style='documentary', seed=123)
            timeline = build_timeline(project, asset_report=assets, editing_plan=plan)
            overlap = timeline['video_track'][0]['transition_out']['duration_frames']
            self.assertGreater(overlap,0, 'Integration fixture needs actual dissolve')
            render_project(project)
            report = mux_project_audio(project)
            self.assertEqual(report['final_qa']['video_frames'], timeline['total_frames'])
            details = report['source_video_audio']
            self.assertEqual(len(details),2)
            fps = Fraction(timeline['time_base']['fps'])
            self.assertEqual(details[1]['start_sample'], round(Fraction(timeline['video_track'][1]['start_frame'],1)*48000/fps))
            self.assertEqual(details[0]['fade_out_samples'], round(Fraction(overlap,1)*48000/fps))
            self.assertEqual(details[1]['fade_in_samples'], round(Fraction(overlap,1)*48000/fps))

            # Decode output and verify audio contribution before, during, after the dissolve.
            pcm = Path(tmp)/'out.pcm'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(project.folder/'output/final.mp4'),
                            '-map','0:a:0','-ac','1','-ar','48000','-f','s16le',str(pcm)],check=True)
            samples = np.frombuffer(pcm.read_bytes(),dtype='<i2').astype(np.float32)
            def amp(freq, t):
                start = int(t*48000)
                piece = samples[start:start+4800]
                oscillator = np.exp(-2j*np.pi*freq*np.arange(len(piece))/48000)
                return abs(np.dot(piece,oscillator))/len(piece)
            scene2_start = timeline['video_track'][1]['start_frame']/float(fps)
            dissolve_duration = overlap/float(fps)
            self.assertGreater(amp(440,0.5),amp(880,0.5)*10)
            self.assertGreater(amp(880,scene2_start+dissolve_duration+0.15),amp(440,scene2_start+dissolve_duration+0.15)*10)
            during = scene2_start+dissolve_duration/2-0.05
            self.assertGreater(amp(440,during),20)
            self.assertGreater(amp(880,during),20)

            # Disable clip audio and confirm automatic muting on rebuild.
            board = project.storyboard
            board['scenes'][1]['source_audio']['enabled'] = False
            project = pm.save_project('demo', storyboard=board)
            assets = validate_assets(project)
            plan = get_default_editing_plan(project,style='documentary',seed=123)
            build_timeline(project,asset_report=assets,editing_plan=plan)
            render_project(project)
            muted = mux_project_audio(project)
            self.assertEqual(len(muted['source_video_audio']),1)

    def test_missing_audio_explicit_required_rejected(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory(prefix='phase111-noaudio-') as tmp:
            pm = ProjectManager(Path(tmp)/'projects',ROOT/'config/default_project.json')
            project = pm.create_project('demo','Test')
            project.config['planning']['require_plan_approval']=False
            board = project.storyboard
            board['scenes'] = [{'scene_id':'001','order':1,'type':'video_clip',
                                'video':'assets/videos/silent.mp4','narration':'',
                                'source_audio':{'enabled':True}}]
            project = pm.save_project('demo', storyboard=board)
            video = project.folder/'assets/videos/silent.mp4'
            subprocess.run(['ffmpeg','-loglevel','error','-y','-f','lavfi','-i','color=s=160x90:r=30:d=1',
                            '-c:v','libx264',str(video)],check=True)
            self.assertEqual(validate_assets(project)['status'],'FAIL')
            board['scenes'][0].pop('source_audio')
            project = pm.save_project('demo',storyboard=board)
            self.assertEqual(validate_assets(project)['status'],'PASS')

if __name__=='__main__': unittest.main()
