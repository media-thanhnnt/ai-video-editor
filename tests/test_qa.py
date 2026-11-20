"""Phase 1.8 QA integration behavior without decoding demo media."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_video_editor.qa import _intervals, _overlap, qa_project, _checked_timeline, QAError


class QATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = SimpleNamespace(folder=self.root, project_id='sample',
                                       config={'output':{'audio_channels':2}})
        self.assets = {'status':'PASS','scene_count':1,'scene_results':[{'scene_id':'001','status':'PASS'}]}
        self.plan = {'scenes':[{'scene_id':'001'}],'style':'calm'}
        self.timeline = {'time_base':{'fps':'30/1','audio_sample_rate':48000},
                         'resolution':{'width':320,'height':180},
                         'video_track':[{'scene_id':'001'}],
                         'voice_track':[{'scene_id':'001','start_sample':0,'end_sample':48000}],
                         'total_frames':30,'total_samples':48000,'total_duration_seconds':1.0,
                         'warnings':[]}

    def _mocks(self):
        return [patch('ai_video_editor.qa._checked_assets', return_value=self.assets),
                patch('ai_video_editor.qa.validate_editing_plan',return_value=self.plan),
                patch('ai_video_editor.qa.read_json',return_value=self.plan),
                patch('ai_video_editor.qa._checked_timeline',return_value=self.timeline),
                patch('ai_video_editor.qa.verify_render_checkpoint',return_value={'render_key':'test'}),
                patch('ai_video_editor.qa.verify_video',return_value={'frames':30}),
                patch('ai_video_editor.qa.inspect_final',return_value={'video_frames':30,'audio_codec':'aac'})]

    def test_all_valid_and_report_atomic(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for mocker in self._mocks(): stack.enter_context(mocker)
            report=qa_project(self.project)
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(set(report['checks']),{'assets','editing_plan','timeline','video_only','final_video_audio'})
        self.assertEqual(report['summary']['total_frames'],30)
        self.assertTrue((self.root/'reports/integration_report.json').is_file())

    def test_failure_sets_exit_compatible_status_and_report(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks=[stack.enter_context(mocker) for mocker in self._mocks()]
            mocks[-1].side_effect=ValueError('AAC missing')
            report=qa_project(self.project)
        self.assertEqual(report['status'],'FAIL')
        self.assertIn('AAC missing',report['checks']['final_video_audio']['error'])
        self.assertEqual(report['checks']['video_only']['status'],'PASS')

    def test_dependencies_block_further_checks(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks=[stack.enter_context(mocker) for mocker in self._mocks()]
            mocks[0].side_effect=QAError('missing')
            report=qa_project(self.project)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['checks']['timeline']['status'],'BLOCKED')
        self.assertEqual(report['checks']['video_only']['status'],'BLOCKED')

    def test_stale_timeline_rejected(self):
        timeline={'time_base':1,'resolution':2,'video_track':3,'voice_track':4,
                  'total_frames':30,'total_samples':40,'planning_cache_key':'old','input_hashes':{}}
        current=dict(timeline,planning_cache_key='new')
        with patch('ai_video_editor.qa.read_json',return_value=timeline), \
             patch('ai_video_editor.qa.validate_timeline'), \
             patch('ai_video_editor.qa.build_timeline',return_value=current):
            with self.assertRaisesRegex(QAError,'Stale timeline'):
                _checked_timeline(self.project,self.assets,self.plan)

    def test_parse_detection_ranges_and_overlap(self):
        self.assertEqual(_intervals('black_start:0 black_end:1.5 black_duration:1.5','black'),[(0.,1.5)])
        self.assertEqual(_intervals('silence_end: 5.2 | silence_duration: 1.5','silence'),[(3.7,5.2)])
        self.assertEqual(_overlap((2,4),(3,5)),1)
        self.assertEqual(_overlap((2,3),(4,5)),0)

if __name__=='__main__': unittest.main()
