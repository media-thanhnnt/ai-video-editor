"""Phase 2.4.1 smoke tests: no FFmpeg, OpenAI, browser or Node dependency."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI = True
except (ImportError, RuntimeError):
    TestClient = None
    create_app = None
    HAS_UI = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(create_app(testing=True, projects_dir=Path(self.tmp.name)/'projects', default_config=DEFAULT))

    def test_dashboard_served(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn('AI Video Editor',response.text)
        self.assertEqual(self.client.get('/api/health').json()['status'],'ok')

    def test_project_creation_and_listing(self):
        created=self.client.post('/api/projects',json={'project_id':'ocean_test','title':'Đại dương'})
        self.assertEqual(created.status_code,201,created.text)
        self.assertEqual(created.json()['script']['status'],'NOT_STARTED')
        self.assertEqual(created.json()['title'],'Đại dương')
        listed=self.client.get('/api/projects').json()
        self.assertEqual(listed['count'],1)
        self.assertEqual(listed['projects'][0]['project_id'],'ocean_test')
        self.assertEqual(self.client.get('/api/projects/ocean_test').status_code,200)

    def test_duplicate_and_bad_ids(self):
        self.client.post('/api/projects',json={'project_id':'test','title':'Test'})
        self.assertEqual(self.client.post('/api/projects',json={'project_id':'test','title':'Test'}).status_code,409)
        self.assertEqual(self.client.post('/api/projects',json={'project_id':'../escape','title':'Test'}).status_code,422)
        self.assertEqual(self.client.get('/api/projects/not_found').status_code,404)

    def test_cross_site_write_rejected(self):
        resp=self.client.post('/api/projects',headers={'Origin':'https://evil.example'},json={'project_id':'test','title':'Test'})
        self.assertEqual(resp.status_code,403)
        self.assertEqual(self.client.get('/api/projects').json()['count'],0)

    def test_no_project_files_served(self):
        self.client.post('/api/projects',json={'project_id':'test','title':'Test'})
        self.assertEqual(self.client.get('/assets/../../projects/test/config.json').status_code,404)

if __name__=='__main__':unittest.main()
