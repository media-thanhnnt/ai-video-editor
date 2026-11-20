"""Phase 1.3 deterministic timing and integration tests."""
import copy
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.project import ProjectManager
from ai_video_editor.timeline import (
    TimelineError, seconds_to_frames, samples_to_frames_ceil, frame_to_sample,
    _transition_overlaps, build_timeline, validate_timeline,
)

ROOT = Path(__file__).resolve().parents[1]


class FrameMathTests(unittest.TestCase):
    def test_30fps(self):
        self.assertEqual(seconds_to_frames(.4, Fraction(30)), 12)
        self.assertEqual(frame_to_sample(12, Fraction(30), 48000), 19200)

    def test_ntsc(self):
        fps = Fraction(30000, 1001)
        self.assertEqual(seconds_to_frames(.4, fps), 12)
        self.assertEqual(frame_to_sample(300, fps, 48000), 480480)
        self.assertEqual(samples_to_frames_ceil(48048, fps, 48000), 30)

    def test_ties_and_ceil(self):
        self.assertEqual(seconds_to_frames(.05, Fraction(30)), 2)
        self.assertEqual(samples_to_frames_ceil(8001, Fraction(30), 48000), 6)

    def test_invalid_input(self):
        with self.assertRaises(TimelineError):
            seconds_to_frames(-.1, Fraction(30))
        with self.assertRaises(TimelineError):
            seconds_to_frames(float('nan'), Fraction(30))

    def test_overlap_caps(self):
        o, w = _transition_overlaps([60, 60, 60], [6]*3, [12]*3, [12,12], 1, 'clamp_and_warn')
        self.assertEqual((o,w), ([12,12],[]))

    def test_overlap_reduced(self):
        o,w = _transition_overlaps([20,20,20], [2]*3, [2]*3, [8,8], 10, 'clamp_and_warn')
        self.assertEqual(o, [4,4]); self.assertEqual(len(w),2)

    def test_policy_fail(self):
        with self.assertRaises(TimelineError):
            _transition_overlaps([20,20], [2,2],[2,2],[8],1,'fail')


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pm = ProjectManager(Path(self.temp.name) / 'projects', ROOT/'config/default_project.json')
        p = self.pm.create_project('demo','Demo')
        sb = copy.deepcopy(p.storyboard)
        for idx in range(1,4):
            sid = str(idx).zfill(3)
            sb['scenes'].append({'scene_id':sid,'order':idx,'narration':'Test',
                                 'image':f'assets/images/scene_{sid}.png',
                                 'audio':f'assets/audio/scene_{sid}.wav'})
        self.project = self.pm.save_project('demo', storyboard=sb)

    def fake_report(self, lengths=(48000, 48000, 48000)):
        entries = {}
        hashes = {'storyboard.json':'abc','config.json':'def'}
        for scene,length in zip(self.project.storyboard['scenes'], lengths):
            entries[scene['audio']] = {'status':'PASS','info':{'decoded_samples':length,'timeline_sample_rate':48000}}
        return {'status':'PASS','file_results':entries,'input_hashes':hashes}

    def test_three_scene_overlap(self):
        t=build_timeline(self.project, asset_report=self.fake_report(), write_output=True)
        # 1 sec audio + .2 head + .8 tail to satisfy 2.0 sec minimum => 60fr per scene
        # two 12-frame overlaps => total 156 frames
        self.assertEqual([v['start_frame'] for v in t['video_track']], [0,48,96])
        self.assertEqual(t['total_frames'],156)
        self.assertEqual(t['total_samples'],249600)
        self.assertEqual([v['start_sample'] for v in t['voice_track']], [9600,86400,163200])
        self.assertTrue((self.project.folder/'timeline.json').exists())
        self.assertFalse(t['approximate_timing'])
        validate_timeline(t)

    def test_no_transition(self):
        t=build_timeline(self.project,asset_report=self.fake_report(),transition='none',write_output=False)
        self.assertEqual(t['total_frames'],180)
        self.assertTrue(all(v['transition_out']['type']=='none' for v in t['video_track']))

    def test_single_scene(self):
        self.project.storyboard['scenes']=self.project.storyboard['scenes'][:1]
        t=build_timeline(self.project,asset_report=self.fake_report(),write_output=False)
        self.assertEqual(t['total_frames'],60)
        self.assertEqual(t['video_track'][-1]['transition_out']['duration_frames'],0)

    def test_failure_from_asset_report(self):
        report=self.fake_report();report['status']='FAIL'
        with self.assertRaises(TimelineError):
            build_timeline(self.project,asset_report=report,write_output=False)
        self.assertFalse((self.project.folder/'timeline.json').exists())

    def test_very_short_voices_and_clamp(self):
        cfg=copy.deepcopy(self.project.config)
        cfg['timing']['min_scene_seconds']=0
        cfg['timing']['pad_tail_seconds']=0
        cfg['timing']['pad_head_seconds']=0
        cfg['transitions']['intro']='none';cfg['transitions']['outro']='none'
        self.project.config=cfg
        t=build_timeline(self.project,asset_report=self.fake_report((800,800,800)),write_output=False)
        self.assertEqual(t['total_frames'],3)
        self.assertEqual([v['transition_out']['duration_frames'] for v in t['video_track']],[0,0,0])
        self.assertTrue(any(w['code']=='TRANSITION_CLAMPED' for w in t['warnings']))

    def test_outro_violation_fail(self):
        cfg=copy.deepcopy(self.project.config)
        cfg['transitions']['intro_outro_seconds']=2.0
        cfg['transitions']['violation_policy']='fail'
        self.project.config=cfg
        with self.assertRaisesRegex(TimelineError,'Outro'):
            build_timeline(self.project,asset_report=self.fake_report(),write_output=False)

    def test_validate_catches_overlap(self):
        t=build_timeline(self.project,asset_report=self.fake_report(),write_output=False)
        t['voice_track'][1]['start_sample']=1
        with self.assertRaises(TimelineError):
            validate_timeline(t)

    def test_fractional_fps(self):
        cfg=copy.deepcopy(self.project.config)
        cfg['output']['fps']='30000/1001'
        self.project.config=cfg
        t=build_timeline(self.project,asset_report=self.fake_report((48048,48048,48048)),write_output=False)
        validate_timeline(t)
        self.assertEqual(t['time_base']['fps'],'30000/1001')


if __name__ == '__main__': unittest.main()
