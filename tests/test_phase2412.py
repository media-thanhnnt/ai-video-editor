"""Phase 2.4.12: render settings, stage progress and right-hand scene panels."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI = True
except (ImportError, RuntimeError):
    HAS_UI = False

from ai_video_editor.api.jobs import JobManager
from ai_video_editor.api.workflow import render_cli

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'


class RenderProgressTests(unittest.TestCase):
    def test_job_reports_progress_and_completion(self):
        jobs = JobManager(workers=1)
        job = jobs.submit('test', 'render', lambda progress: (progress('2/3 scenes', 70), {'done': True})[1])
        import time
        for _ in range(100):
            final = jobs.get('test', job['job_id'])
            if final['status'] == 'succeeded':
                break
            time.sleep(0.01)
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['percent'], 100)

    def test_timeout_validation_and_no_wallclock_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'test'; folder.mkdir()
            (folder / 'storyboard.json').write_text(json.dumps({'scenes': [{}]}))
            for invalid in (-1, 525601, 1.2):
                with self.assertRaises(ValueError):
                    render_cli('test', Path(tmp), DEFAULT, lambda *a: None,
                               timeout_minutes=invalid)
            class Finished:
                pid = 123
                returncode = 0
                def poll(self):
                    return 0
            reported = []
            with patch('ai_video_editor.api.workflow.subprocess.Popen', return_value=Finished()):
                result = render_cli('test', Path(tmp), DEFAULT,
                                    lambda message, percent=None: reported.append(percent),
                                    timeout_minutes=0)
            self.assertEqual(result['status'], 'finished')
            self.assertEqual(reported, [1, 99])

class ScriptRevisionBindingTests(unittest.TestCase):
    def test_old_generated_voice_is_not_auto_bound_after_script_edit(self):
        from ai_video_editor.project import ProjectManager
        from ai_video_editor.agent.script_generator import create_script_draft, approve_script
        from ai_video_editor.agent.asset_workflow import save_generated_asset
        from ai_video_editor.api.workflow import prepare_manifest, bind_generated
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProjectManager(Path(tmp) / 'projects', DEFAULT)
            project = pm.create_project('demo', 'Demo')
            script = {'title': 'Demo', 'language': 'vi', 'style': 'documentary',
                      'target_duration_seconds': 10, 'summary': 'Demo', 'script': 'New voice',
                      'scenes': [{'scene_id': '001', 'type': 'image', 'narration': 'New voice',
                                  'prompt': 'Ocean image', 'estimated_duration_seconds': 10}]}
            create_script_draft(project, script, source='manual')
            approve_script(project)
            prepare_manifest(project)
            save_generated_asset(project, '001', 'audio', b'ID3old', '.mp3', 'mock',
                                 source_prompt='Old voice')
            (project.folder / 'script_revision_pending.json').write_text('{}')
            bound = bind_generated(project)
            self.assertFalse(any(item['role'] == 'voice_file' for item in bound['bound']))
            save_generated_asset(project, '001', 'audio', b'ID3new', '.mp3', 'mock',
                                 source_prompt='New voice')
            bound = bind_generated(project)
            self.assertTrue(any(item['role'] == 'voice_file' for item in bound['bound']))

@unittest.skipUnless(HAS_UI, 'GUI dependencies unavailable')
class GuiLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.client = TestClient(create_app(testing=True, projects_dir=Path(temp.name) / 'projects',
                                           default_config=DEFAULT))
    def test_scene_panels_and_render_timeout_field(self):
        for page in ('/scene-planner', '/render-studio'):
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200)
            self.assertIn('scene-links', response.text)
            self.assertIn('scene-nav', response.text)
        studio = self.client.get('/render-studio').text
        self.assertIn('id="render-timeout"', studio)
        self.assertIn('id="render-percent"', studio)
    def test_invalid_timeout_is_rejected(self):
        self.client.post('/api/projects', json={'project_id': 'test', 'title': 'Test'})
        response = self.client.post('/api/projects/test/render/start',
                                    json={'confirmed': True, 'timeout_minutes': -1})
        self.assertEqual(response.status_code, 422)
