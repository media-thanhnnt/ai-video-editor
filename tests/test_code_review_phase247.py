"""Regression tests for CODE_REVIEW_PHASE2 and shared UI navigation."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.project import ValidationError, validate_sequence
from ai_video_editor.agent.manifest import validate_brief
from ai_video_editor.agent.scene_planner import generate_openai_scene_plan
from ai_video_editor.agent.script_generator import openai_generate

ROOT = Path(__file__).resolve().parents[1]

class ReviewCoreTests(unittest.TestCase):
    def test_sequence_invalid_path_rejected_early(self):
        brief={'title':'Demo','scenes':[{'scene_id':'001','type':'image_sequence','sequence':{'frames_dir':'assets/elsewhere','frame_pattern':'frame_%04d.png','frame_count':25,'fps':'30/1'}}]}
        with self.assertRaises(ValidationError): validate_brief(brief)

    def test_sequence_valid(self):
        self.assertEqual(validate_sequence({'frames_dir':'assets/sequences/clip','frame_pattern':'frame_%04d.png','frame_count':25,'fps':'30/1'},'001')['frame_count'],25)

    def test_openai_non_json_handled(self):
        from io import BytesIO
        from contextlib import closing
        class BadResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'<html>unavailable</html>'
        def opener(*args, **kwargs): return BadResponse()
        with self.assertRaisesRegex(ValidationError, 'Invalid structured JSON'):
            openai_generate('Make an ocean film', api_key='fake-key', opener=opener)
        with self.assertRaisesRegex(ValidationError, 'Invalid OpenAI scene-plan JSON'):
            generate_openai_scene_plan({'title':'Test','scenes':[]}, api_key='fake-key', opener=opener)

    def test_no_secrets_in_gitignore(self):
        data=(ROOT/'.gitignore').read_text()
        for pattern in ('.env', '.env.*', '*.pem', '*.key', 'secrets.json'):
            self.assertIn(pattern,data)

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI=True
except (ImportError, RuntimeError):
    HAS_UI=False

@unittest.skipUnless(HAS_UI,'GUI deps unavailable')
class ReviewGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.client=TestClient(create_app(testing=True, projects_dir=Path(self.temp.name)/'projects',default_config=ROOT/'config/default_project.json'))

    def test_all_pages_share_sidebar(self):
        for url in ('/', '/script-editor','/scene-planner','/asset-workspace','/render-studio','/api-settings'):
            with self.subTest(url=url):
                res=self.client.get(url)
                self.assertEqual(res.status_code,200)
                self.assertIn('id="app-sidebar"',res.text)
                self.assertIn('/static/sidebar.js',res.text)
                self.assertNotIn('Local Studio · Phase 2.4.1',res.text)

    def test_sidebar_has_six_links_and_api_settings(self):
        self.assertEqual(self.client.get('/static/sidebar.css').status_code,200)
        js=self.client.get('/static/sidebar.js')
        self.assertEqual(js.status_code,200)
        for name in ('Dashboard','Script Editor','Scene Planner','Asset Workspace','Render Studio','API Settings'):
            self.assertIn(name,js.text)
        self.assertEqual(self.client.get('/static/secrets.json').status_code,404)

    def test_host_header_rejected(self):
        self.assertEqual(self.client.get('/api/projects',headers={'Host':'evil.example'}).status_code,400)

    def test_invalid_origin_port_is_403(self):
        self.assertEqual(self.client.post('/api/projects',headers={'Origin':'http://localhost:invalid'},json={'project_id':'hello','title':'Hello'}).status_code,403)

if __name__=='__main__': unittest.main()
