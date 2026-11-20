"""Phase 1.10.3: precision, cache identity, portability, and capability regressions."""
import copy
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.ffmpeg_support import check_ffmpeg
from ai_video_editor.planner import get_default_editing_plan, plan_cache_key, validate_editing_plan
from ai_video_editor.project import ProjectManager, ValidationError
from ai_video_editor.renderer import (_checkpoint_matches_output, _checkpoint_path,
                                       IMAGE_MOTION_CACHE_VERSION, compute_render_identity)

ROOT=Path(__file__).resolve().parents[1]

class Phase1103Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.pm=ProjectManager(self.root/'projects',ROOT/'config/default_project.json')
        self.project=self.pm.create_project('demo','Demo')
        board=copy.deepcopy(self.project.storyboard)
        board['scenes']=[{'scene_id':'001','order':1,'narration':'x','image':'assets/images/001.png','audio':'assets/audio/001.wav'}]
        self.project=self.pm.save_project('demo',storyboard=board)

    def test_approval_and_padding_do_not_stale_plan(self):
        plan=get_default_editing_plan(self.project)
        self.project.config['planning']['require_plan_approval']=True
        self.project.config['timing']['pad_head_seconds']=2.0
        self.project=self.pm.save_project('demo',config=self.project.config)
        self.assertEqual(plan['plan_meta']['cache_key'],plan_cache_key(self.project,plan['style'],plan['plan_meta']['seed']))
        validate_editing_plan(self.project,plan)

    def test_transition_change_does_stale_plan(self):
        plan=get_default_editing_plan(self.project)
        self.project.config['transitions']['violation_policy']='fail'
        self.project=self.pm.save_project('demo',config=self.project.config)
        with self.assertRaisesRegex(ValidationError,'Stale'):
            validate_editing_plan(self.project,plan)

    def test_motion_cache_version_separate_from_legacy_engine(self):
        from ai_video_editor.renderer import ENGINE_VERSION
        self.assertNotEqual(ENGINE_VERSION,IMAGE_MOTION_CACHE_VERSION)
        self.assertEqual(IMAGE_MOTION_CACHE_VERSION,'image-motion-1.10.3')

    def test_portable_checkpoint_paths(self):
        out=self.project.folder/'output'/'video_only.mp4'
        self.assertEqual(_checkpoint_path(self.project,out),'output/video_only.mp4')
        self.assertTrue(_checkpoint_matches_output(self.project,'output/video_only.mp4',out))
        self.assertTrue(_checkpoint_matches_output(self.project,str(out),out))
        self.assertTrue(_checkpoint_matches_output(self.project,'C:/moved/demo/output/video_only.mp4',out))
        self.assertFalse(_checkpoint_matches_output(self.project,'output/fake.mp4',out))
        self.assertFalse(_checkpoint_matches_output(self.project,'../video_only.mp4',out))

    def test_ffmpeg_version_6_0_rejected(self):
        with patch('ai_video_editor.ffmpeg_support.subprocess.run') as run:
            run.return_value.stdout='ffmpeg version 6.0 Copyright'
            with self.assertRaisesRegex(ValidationError,'6.1'):
                check_ffmpeg()

    def test_ffmpeg_versions_supported(self):
        for version,expected in [('6.1',6),('n6.1.4',6),('7.0',7),('9.0.2-full_build',9)]:
            with self.subTest(version=version),patch('ai_video_editor.ffmpeg_support.subprocess.run') as run:
                run.return_value.stdout='ffmpeg version '+version+' Copyright'
                self.assertEqual(check_ffmpeg(),expected)

    def test_audio_limiter_latency_compensated_in_pipeline(self):
        source=(ROOT/'src/ai_video_editor/audio.py').read_text(encoding='utf8')
        self.assertIn(':level=0:latency=1[aout]',source)

    def test_font_directory_is_not_hardcoded(self):
        source=(ROOT/'src/ai_video_editor/renderer.py').read_text(encoding='utf8')
        self.assertIn('font.parent.relative_to(project.folder.resolve())',source)
        self.assertNotIn('fontsdir=assets/fonts',source)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg unavailable')
    def test_click_position_after_alimiter_pcm(self):
        """Check sample-exact placement BEFORE lossy AAC, for both track-style inputs.

        FFmpeg WAV decoding makes exact sample positions observable. Final MP4/AAC
        carries encoder delay metadata and may need priming-aware decoding.
        """
        samples=96000
        for mode in ('video_source','additional_track'):
            with self.subTest(mode=mode):
                input_path=self.root/f'{mode}.wav'
                output_path=self.root/f'{mode}_out.wav'
                with wave.open(str(input_path),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
                    a=bytearray(samples*2)
                    struct.pack_into('<h',a,48000*2,25000)
                    wav.writeframes(a)
                graph=('aresample=48000,aformat=channel_layouts=mono,'
                       'alimiter=limit=0.89:level=0:latency=1')
                subprocess.run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
                                '-i',str(input_path),'-af',graph,
                                '-c:a','pcm_s16le',str(output_path)],check=True)
                with wave.open(str(output_path),'rb') as wav:
                    raw=wav.readframes(wav.getnframes())
                    count=wav.getnframes()
                decoded=struct.unpack('<'+'h'*(len(raw)//2),raw)
                peak=max(range(len(decoded)),key=lambda i:abs(decoded[i]))
                self.assertLessEqual(abs(peak-48000),2)
                self.assertEqual(count,samples)

if __name__=='__main__': unittest.main()
