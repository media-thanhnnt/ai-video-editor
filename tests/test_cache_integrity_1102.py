"""End-to-end regressions: stale video, preserved plans, audio-only and burn-in edits."""
from __future__ import annotations
import copy
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image
from ai_video_editor.project import ProjectManager, atomic_write_json
from ai_video_editor.planner import get_default_editing_plan, validate_editing_plan
from ai_video_editor.timeline import build_timeline
from ai_video_editor.assets import validate_assets
from ai_video_editor.renderer import render_project, verify_render_checkpoint, RenderError
from ai_video_editor.planner import PlanError
from ai_video_editor.audio import mux_project_audio
from ai_video_editor.cli import main as cli_main

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class CacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pm=ProjectManager(Path(self.temp.name)/'projects',ROOT/'config/default_project.json')
        project=self.pm.create_project('demo','Cache integrity')
        config=project.config
        config['output'].update(width=160,height=90,encoder_preset='ultrafast')
        config['motion']['supersample_factor']=1
        config['transitions'].update(intro='none',outro='none')
        config['timing'].update(pad_head_seconds=0,pad_tail_seconds=0)
        config['subtitles']['mode']='sidecar'
        board=project.storyboard
        board['scenes']=[{'scene_id':'001','order':1,'narration':'hello','type':'image',
                          'image':'assets/images/scene_001.png','audio':'assets/audio/voice.wav'}]
        board['subtitles']=[{'start_seconds':0,'end_seconds':0.4,'text':'Hello'}]
        self.project=self.pm.save_project('demo',config=config,storyboard=board)
        Image.new('RGB',(160,90),(220,30,30)).save(self.project.folder/'assets/images/scene_001.png')
        with wave.open(str(self.project.folder/'assets/audio/voice.wav'),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000)
            w.writeframes(b'\0\0'*48000)

    def prepare(self):
        assets=validate_assets(self.project)
        plan=get_default_editing_plan(self.project,style='calm',seed=99)
        plan['plan_meta']['approved_by_user']=True
        atomic_write_json(self.project.folder/'editing_plan.json',plan)
        timeline=build_timeline(self.project,asset_report=assets,editing_plan=plan)
        return plan,timeline

    def test_approval_gate_and_cli_approval(self):
        self.project.config['planning']['require_plan_approval']=True
        self.project=self.pm.save_project('demo',config=self.project.config)
        assets=validate_assets(self.project)
        plan=get_default_editing_plan(self.project,style='documentary')
        build_timeline(self.project,asset_report=assets,editing_plan=plan)
        with self.assertRaisesRegex(PlanError,'requires approval'):
            render_project(self.project)
        from ai_video_editor.project import read_json
        result=cli_main(['--projects-dir',str(self.pm.projects_dir),
                         '--default-config',str(ROOT/'config/default_project.json'),
                         'approve-plan','demo'])
        self.assertEqual(result,0)
        plan=read_json(self.project.folder/'editing_plan.json')
        self.assertTrue(plan['plan_meta']['approved_by_user'])
        result=render_project(self.project)
        self.assertEqual(result['status'],'PASS')

    def test_stale_checkpoint_rejected_in_audio(self):
        plan,timeline=self.prepare()
        render_project(self.project)
        verify_render_checkpoint(self.project,plan,timeline,self.project.folder/'output/video_only.mp4')
        with open(self.project.folder/'output/video_only.mp4','ab') as f:
            f.write(b'tamper')
        with self.assertRaisesRegex(RenderError,'SHA-256'):
            mux_project_audio(self.project)

    def test_subtitle_sidecar_and_extra_audio_keep_video_cache(self):
        plan,timeline=self.prepare()
        first=render_project(self.project)
        self.assertFalse(first['cached_output'])
        board=copy.deepcopy(self.project.storyboard)
        board['subtitles'][0]['text']='Changed subtitle only'
        board['audio_tracks']=[{'file':'assets/audio/voice.wav','start_seconds':0.0,'volume':0.05}]
        self.project=self.pm.save_project('demo',storyboard=board)
        plan2=validate_editing_plan(self.project,plan)
        self.assertTrue(plan2['plan_meta']['approved_by_user'])
        assets=validate_assets(self.project)
        timeline2=build_timeline(self.project,asset_report=assets,editing_plan=plan2)
        self.assertEqual(timeline['input_hashes'],timeline2['input_hashes'])
        second=render_project(self.project)
        self.assertTrue(second['cached_output'])
        mux_project_audio(self.project)
        self.assertIn('Changed subtitle', (self.project.folder/'output/subtitles.srt').read_text())

    def test_burn_in_subtitle_change_reencodes_only_assembly(self):
        self.project.config['subtitles']['mode']='both'
        self.project=self.pm.save_project('demo',config=self.project.config)
        plan,timeline=self.prepare()
        first=render_project(self.project)
        self.assertFalse(first['cached_output'])
        board=copy.deepcopy(self.project.storyboard)
        board['subtitles'][0]['text']='Burn-in edited'
        self.project=self.pm.save_project('demo',storyboard=board)
        plan2=validate_editing_plan(self.project,plan)
        timeline2=build_timeline(self.project,asset_report=validate_assets(self.project),editing_plan=plan2)
        second=render_project(self.project)
        self.assertFalse(second['cached_output'])
        self.assertEqual(second['cached_scenes'],1)
        mux_project_audio(self.project)

    def test_compose_keeps_approved_custom_plan(self):
        plan,_=self.prepare()
        result=cli_main(['--projects-dir',str(self.pm.projects_dir),
                         '--default-config',str(ROOT/'config/default_project.json'),
                         'compose','demo'])
        self.assertEqual(result,0)
        from ai_video_editor.project import read_json
        stored=read_json(self.project.folder/'editing_plan.json')
        self.assertEqual(stored['style'],'calm')
        self.assertEqual(stored['plan_meta']['seed'],99)
        self.assertTrue(stored['plan_meta']['approved_by_user'])
