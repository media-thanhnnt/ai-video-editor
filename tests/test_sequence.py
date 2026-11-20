"""Phase 1.9 sequence contract and real FFmpeg end-to-end tests."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image

from ai_video_editor.project import ProjectManager, ValidationError, validate_storyboard
from ai_video_editor.assets import validate_assets
from ai_video_editor.planner import get_default_editing_plan
from ai_video_editor.timeline import build_timeline, TimelineError
from ai_video_editor.renderer import render_project, verify_video
from ai_video_editor.audio import mux_project_audio
from ai_video_editor.qa import qa_project

ROOT = Path(__file__).resolve().parents[1]


def scene(count=6, fps='30/1'):
    return {'scene_id':'001','order':1,'type':'image_sequence','narration':'',
            'sequence':{'frames_dir':'assets/sequences/throw_ball',
                        'frame_pattern':'frame_%04d.png','frame_count':count,'fps':fps}}


class SequenceTests(unittest.TestCase):
    def test_accept_valid_sequence(self):
        raw={'schema_version':'1.1','project_id':'sequence_demo','title':'Sequence',
             'voice_mode':'per_scene','scenes':[scene(90)]}
        self.assertEqual(validate_storyboard(raw)['scenes'][0]['sequence']['frame_count'],90)

    def test_reject_path_traversal(self):
        raw={'schema_version':'1.1','project_id':'sequence_demo','title':'Sequence',
             'voice_mode':'per_scene','scenes':[scene(90)]}
        raw['scenes'][0]['sequence']['frames_dir']='../outside'
        with self.assertRaises(ValidationError): validate_storyboard(raw)

    def test_reject_unsafe_pattern(self):
        raw={'schema_version':'1.1','project_id':'sequence_demo','title':'Sequence',
             'voice_mode':'per_scene','scenes':[scene(90)]}
        raw['scenes'][0]['sequence']['frame_pattern']='../../frame_%04d.png'
        with self.assertRaises(ValidationError): validate_storyboard(raw)

    def test_reject_nonpositive_frame_count(self):
        raw={'schema_version':'1.1','project_id':'sequence_demo','title':'Sequence',
             'voice_mode':'per_scene','scenes':[scene(0)]}
        with self.assertRaises(ValidationError): validate_storyboard(raw)

    def test_missing_frame_fails_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            pm = ProjectManager(Path(temp)/'projects',ROOT/'config'/'default_project.json')
            project=pm.create_project('sequence_demo','Ball')
            project.storyboard['scenes']=[scene(3)]
            project=pm.save_project('sequence_demo',storyboard=project.storyboard)
            frames=project.folder/'assets'/'sequences'/'throw_ball'
            frames.mkdir(parents=True)
            Image.new('RGB',(160,90)).save(frames/'frame_0001.png')
            Image.new('RGB',(160,90)).save(frames/'frame_0003.png')
            report=validate_assets(project)
            self.assertEqual(report['status'],'FAIL')
            self.assertIn('frame_0002.png', str(report['scene_results'][0]['errors']))

    def test_mismatched_image_dimensions_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            pm = ProjectManager(Path(temp)/'projects',ROOT/'config'/'default_project.json')
            project=pm.create_project('sequence_demo','Ball')
            project.storyboard['scenes']=[scene(2)]
            project=pm.save_project('sequence_demo',storyboard=project.storyboard)
            frames=project.folder/'assets'/'sequences'/'throw_ball'
            frames.mkdir(parents=True)
            Image.new('RGB',(160,90)).save(frames/'frame_0001.png')
            Image.new('RGB',(320,180)).save(frames/'frame_0002.png')
            report=validate_assets(project)
            self.assertEqual(report['status'],'FAIL')
            self.assertIn('dimensions',str(report['scene_results'][0]['errors']))

    def test_mixed_still_voice_and_sequence(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg not available')
        import wave
        with tempfile.TemporaryDirectory() as temp:
            pm = ProjectManager(Path(temp)/'projects',ROOT/'config'/'default_project.json')
            project=pm.create_project('sequence_demo','Mixed')
            project.config['planning']['require_plan_approval']=False
            project.config['output']['width']=160
            project.config['output']['height']=90
            project.config['output']['encoder_preset']='ultrafast'
            project.config['motion']['supersample_factor']=1
            project.config['transitions']['intro']='none'
            project.config['transitions']['outro']='none'
            first={'scene_id':'001','order':1,'narration':'Hello',
                   'image':'assets/images/scene_001.png','audio':'assets/audio/scene_001.wav'}
            second=scene(90)
            second.update(scene_id='002',order=2)
            project.storyboard['scenes']=[first,second]
            project=pm.save_project('sequence_demo',config=project.config,storyboard=project.storyboard)
            Image.new('RGB',(160,90),'red').save(project.folder/'assets/images/scene_001.png')
            with wave.open(str(project.folder/'assets/audio/scene_001.wav'),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000)
                out.writeframes(b'\0\0'*48000)
            frames=project.folder/'assets/sequences/throw_ball'
            frames.mkdir(parents=True)
            for n in range(1,91):
                Image.new('RGB',(160,90),(n%256,5,10)).save(frames/f'frame_{n:04d}.png')
            assets=validate_assets(project)
            self.assertEqual(assets['status'],'PASS')
            plan=get_default_editing_plan(project,style='documentary',seed=123)
            timeline=build_timeline(project,asset_report=assets,editing_plan=plan)
            self.assertEqual(len(timeline['video_track']),2)
            self.assertEqual(len(timeline['voice_track']),1)
            rendered=render_project(project)
            self.assertEqual(rendered['video']['frames'],timeline['total_frames'])
            mux_project_audio(project)
            result=qa_project(project)
            self.assertEqual(result['status'],'PASS',result['checks'])

    def test_end_to_end_silent_sequence(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg not available')
        with tempfile.TemporaryDirectory() as temp:
            pm = ProjectManager(Path(temp)/'projects',ROOT/'config'/'default_project.json')
            pm.create_project('sequence_demo','Ball')
            project = pm.load_project('sequence_demo')
            project.config['planning']['require_plan_approval'] = False
            project.config['output']['width']=160
            project.config['output']['height']=90
            project.config['output']['encoder_preset']='ultrafast'
            project.config['motion']['supersample_factor']=1
            project.config['timing']['pad_head_seconds']=0
            project.config['timing']['pad_tail_seconds']=0
            project.config['transitions']['intro']='none'
            project.config['transitions']['outro']='none'
            project.storyboard['scenes']=[scene(90)]
            project=pm.save_project('sequence_demo',config=project.config,storyboard=project.storyboard)
            frames=project.folder/'assets'/'sequences'/'throw_ball'
            frames.mkdir(parents=True,exist_ok=True)
            for index in range(1,91):
                Image.new('RGB',(160,90), (index*2%256,10,20)).save(frames/f'frame_{index:04d}.png')
            report=validate_assets(project)
            self.assertEqual(report['status'],'PASS')
            plan=get_default_editing_plan(project,style='documentary',seed=123)
            self.assertEqual(plan['scenes'][0]['motion'],'static')
            timeline=build_timeline(project,asset_report=report,editing_plan=plan)
            self.assertEqual(timeline['total_frames'],90)
            self.assertEqual(timeline['voice_track'],[])
            rendered=render_project(project)
            self.assertEqual(rendered['video']['frames'],90)
            self.assertEqual(rendered['cached_scenes'],0)
            reused=render_project(project)
            self.assertEqual(reused['cached_scenes'],1)
            self.assertTrue(reused['cached_output'])
            mux=mux_project_audio(project)
            self.assertEqual(mux['final_qa']['video_frames'],90)
            self.assertEqual(mux['final_qa']['expected_audio_samples'],144000)
            result=qa_project(project)
            self.assertEqual(result['status'],'PASS',result['checks'])


if __name__ == '__main__':
    unittest.main()
