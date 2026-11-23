"""Phase 2.4.11: user-set daily limits have no built-in numeric ceiling."""
import tempfile
import unittest
from pathlib import Path

from ai_video_editor.project import ProjectManager
from ai_video_editor.api.limits import usage, set_limits, reserve

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI = True
except (ImportError, RuntimeError):
    HAS_UI = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'

class LocalLimitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pm = ProjectManager(Path(temp.name)/'projects', DEFAULT)
        self.project = self.pm.create_project('quota_test','Quota Test')

    def test_user_can_choose_more_than_old_fixed_caps(self):
        result = set_limits(self.project, image=320, audio=500)
        self.assertEqual(result['limits'], {'image':320, 'audio':500})
        self.assertEqual(usage(self.project)['remaining']['image'], 320)
        self.assertEqual(usage(self.project)['remaining']['audio'], 500)

    def test_existing_used_attempts_survive_limit_change(self):
        set_limits(self.project, image=20, audio=10)
        reserve(self.project, 'image')
        reserve(self.project, 'image')
        state = set_limits(self.project, image=32, audio=10)
        self.assertEqual(state['used']['image'], 2)
        self.assertEqual(state['remaining']['image'], 30)

    def test_zero_disables_and_negative_rejected(self):
        set_limits(self.project, image=0, audio=0)
        with self.assertRaises(ValueError):
            reserve(self.project, 'image')
        with self.assertRaises(ValueError):
            set_limits(self.project, image=-1, audio=3)

@unittest.skipUnless(HAS_UI, 'GUI deps missing')
class BatchSchemaTests(unittest.TestCase):
    def test_more_than_50_scenes_allowed_by_request_schema(self):
        from ai_video_editor.api.app import BatchAssetsRequest
        body = BatchAssetsRequest(scene_ids=[f'{i:03d}' for i in range(1, 61)], kind='image', confirmed=True)
        self.assertEqual(len(body.scene_ids), 60)

@unittest.skipUnless(HAS_UI, 'GUI deps missing')
class LimitApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pm = ProjectManager(Path(temp.name)/'projects', DEFAULT)
        self.pm.create_project('quota_test','Quota Test')
        self.client = TestClient(create_app(testing=True, projects_dir=self.pm.projects_dir, default_config=DEFAULT))
        self.base = '/api/projects/quota_test'

    def test_api_accepts_user_selected_limits_over_legacy_maximum(self):
        r = self.client.put(self.base+'/assets/limits', json={'image':320,'audio':500})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['limits']['image'],320)
        status = self.client.get(self.base+'/assets/status')
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()['api_usage']['limits']['audio'],500)

    def test_gui_has_no_hardcoded_number_max(self):
        html = self.client.get('/asset-workspace').text
        self.assertIn('id="limitImage"', html)
        self.assertNotIn('id="limitImage" type="number" min="0" max="20"', html)
        self.assertNotIn('id="limitAudio" type="number" min="0" max="50"', html)
        self.assertIn('Bạn có thể tăng giới hạn', html)

if __name__ == '__main__':
    unittest.main()
