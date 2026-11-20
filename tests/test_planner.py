"""Phase 1.4 deterministic planner and planned timeline integration tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_video_editor.planner import (PlanError, get_default_editing_plan,
                                      validate_editing_plan, merge_plan_into_storyboard)
from ai_video_editor.project import ProjectManager
from ai_video_editor.timeline import build_timeline

ROOT = Path(__file__).resolve().parents[1]


class PlannerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pm = ProjectManager(Path(temp.name) / 'projects', ROOT / 'config/default_project.json')
        p = self.pm.create_project('plan_demo', 'Demo')
        sb = copy.deepcopy(p.storyboard)
        for i in range(1, 11):
            sid = f'{i:03d}'
            sb['scenes'].append({'scene_id': sid, 'order': i, 'narration': f'Câu {i}',
                                 'image': f'assets/images/scene_{sid}.png',
                                 'audio': f'assets/audio/scene_{sid}.wav'})
        self.project = self.pm.save_project('plan_demo', storyboard=sb)

    def plan(self, **kwargs):
        return get_default_editing_plan(self.project, write_output=False, **kwargs)

    def test_repeatability(self):
        self.assertEqual(self.plan(), self.plan())

    def test_seed_changes_choices(self):
        a = self.plan(seed=123)
        b = self.plan(seed=456)
        self.assertNotEqual(a['scenes'], b['scenes'])
        self.assertNotEqual(a['plan_meta']['cache_key'], b['plan_meta']['cache_key'])

    def test_styles(self):
        for style in ('calm','documentary','energetic'):
            p = self.plan(style=style)
            validate_editing_plan(self.project, p)
            self.assertEqual(p['style'], style)
        self.assertTrue(all(s['motion_intensity'] == 'subtle' for s in self.plan(style='calm')['scenes']))

    def test_scene_order_and_last(self):
        p = self.plan()
        self.assertEqual([s['scene_id'] for s in p['scenes']], [f'{i:03d}' for i in range(1,11)])
        self.assertEqual(p['scenes'][-1]['transition_to_next'], 'none')
        self.assertIsNone(p['scenes'][-1]['transition_length'])

    def test_merge_does_not_mutate(self):
        before = copy.deepcopy(self.project.storyboard)
        merged = merge_plan_into_storyboard(self.project, self.plan())
        self.assertEqual(self.project.storyboard, before)
        for original, modified in zip(before['scenes'],merged['scenes']):
            self.assertEqual({k:v for k,v in modified.items() if k != 'editing'}, original)
            self.assertIn('motion', modified['editing'])

    def test_atomic_file_and_idempotent_bytes(self):
        first = get_default_editing_plan(self.project)
        path = self.project.folder / 'editing_plan.json'
        b1 = path.read_bytes()
        get_default_editing_plan(self.project)
        self.assertEqual(path.read_bytes(), b1)
        self.assertEqual(json.loads(b1), first)

    def test_reject_extra_field(self):
        plan = self.plan()
        plan['scenes'][0]['narration'] = 'injection'
        with self.assertRaises(PlanError):
            validate_editing_plan(self.project, plan)

    def test_reject_invalid_enum(self):
        plan = self.plan()
        plan['scenes'][0]['motion'] = 'shell_command'
        with self.assertRaises(PlanError):
            validate_editing_plan(self.project, plan)

    def test_reject_missing_unknown_or_reordered_ids(self):
        for mode in ('missing','unknown','reordered'):
            plan = self.plan()
            if mode == 'missing':
                plan['scenes'].pop()
            elif mode == 'unknown':
                plan['scenes'][0]['scene_id'] = 'x'
            else:
                plan['scenes'][0],plan['scenes'][1] = plan['scenes'][1],plan['scenes'][0]
            with self.subTest(mode=mode), self.assertRaises(PlanError):
                validate_editing_plan(self.project, plan)

    def test_reject_wrong_final_transition(self):
        plan = self.plan()
        plan['scenes'][-1]['transition_to_next'] = 'crossfade'
        plan['scenes'][-1]['transition_length'] = 'short'
        with self.assertRaises(PlanError):
            validate_editing_plan(self.project, plan)

    def test_reject_invalid_none_length(self):
        plan = self.plan()
        plan['scenes'][0]['transition_to_next'] = 'none'
        plan['scenes'][0]['transition_length'] = 'short'
        with self.assertRaises(PlanError):
            validate_editing_plan(self.project, plan)

    def test_stale_plan_rejected(self):
        plan = self.plan()
        self.project.storyboard['scenes'][0]['image'] = 'assets/images/replaced.png'
        with self.assertRaises(PlanError):
            validate_editing_plan(self.project, plan)

    def test_invalid_style_seed(self):
        with self.assertRaises(PlanError):
            self.plan(style='evil')
        with self.assertRaises(PlanError):
            self.plan(seed=True)

    def fake_report(self):
        entries = {}
        for scene in self.project.storyboard['scenes']:
            entries[scene['audio']] = {'status': 'PASS', 'info': {'decoded_samples': 24000, 'timeline_sample_rate': 48000}}
        return {'status':'PASS','file_results':entries,'input_hashes':{'config.json':'x'}}

    def test_planned_timeline(self):
        p = self.plan()
        t = build_timeline(self.project, asset_report=self.fake_report(), editing_plan=p, write_output=False)
        self.assertEqual(t['planning_source'], 'rule_based')
        self.assertEqual(t['planning_cache_key'], p['plan_meta']['cache_key'])
        self.assertEqual([v['motion']['type'] for v in t['video_track']], [s['motion'] for s in p['scenes']])
        for v, sp in zip(t['video_track'][:-1],p['scenes'][:-1]):
            if sp['transition_to_next'] == 'none':
                self.assertEqual(v['transition_out']['duration_frames'], 0)
            else:
                self.assertEqual(v['transition_out']['type'], sp['transition_to_next'])

    def test_stale_plan_does_not_overwrite_timeline(self):
        p = self.plan()
        output = self.project.folder / 'timeline.json'
        output.write_text('previous', encoding='utf8')
        p['scenes'].pop()
        with self.assertRaises(PlanError):
            build_timeline(self.project, asset_report=self.fake_report(), editing_plan=p)
        self.assertEqual(output.read_text(encoding='utf8'), 'previous')


if __name__ == '__main__':
    unittest.main()
