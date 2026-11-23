"""Phase 2.4.2: actual Phase 2.2 business logic, mocked billable call."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI = True
except (ImportError, RuntimeError):
    TestClient = None
    create_app = None
    HAS_UI = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SAMPLE = {'title':'Ocean','language':'Vietnamese','style':'documentary','target_duration_seconds':10,
          'summary':'Ocean scene','script':'The sea is vast.',
          'scenes':[{'scene_id':'001','type':'video_clip','narration':'The sea is vast.',
                     'prompt':'Wide ocean shot','estimated_duration_seconds':10}]}
USAGE = {'provider':'openai','model':'test-model','input_tokens':5,'output_tokens':8,'total_tokens':13,'estimated_cost_usd':None}

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class ScriptEditorGuiTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'projects'
        self.client=TestClient(create_app(testing=True, projects_dir=self.root,default_config=DEFAULT))
        secret_patch = patch('ai_video_editor.api.app._provider_key', return_value='mock-provider-key')
        secret_patch.start(); self.addCleanup(secret_patch.stop)
        r=self.client.post('/api/projects',json={'project_id':'ocean','title':'Ocean'})
        self.assertEqual(r.status_code,201,r.text)
        self.base='/api/projects/ocean/script'

    def generate(self):
        with patch('ai_video_editor.api.app.openai_generate',return_value=(copy.deepcopy(SAMPLE),USAGE)) as mock:
            r=self.client.post(self.base+'/generate',json={'requirement':'Make an ocean video','model':'test-model'})
        self.assertEqual(r.status_code,201,r.text)
        mock.assert_called_once_with('Make an ocean video',model='gpt-6-luna',api_key='mock-provider-key')
        return r

    def test_editor_page_and_no_existing_script(self):
        r=self.client.get('/script-editor');self.assertEqual(r.status_code,200)
        self.assertIn('Generate Script',r.text)
        self.assertEqual(self.client.get(self.base).status_code,404)
        self.assertEqual(self.client.get('/api/health').json()['phase'],'2.5.1.3')

    def test_generate_read_approve_export(self):
        self.generate()
        draft=self.client.get(self.base).json();self.assertEqual(draft['status'],'AWAITING_SCRIPT_APPROVAL')
        self.assertEqual(draft['usage']['total_tokens'],13)
        r=self.client.post(self.base+'/approve');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get(self.base).json()['status'],'SCRIPT_APPROVED')
        r=self.client.post(self.base+'/export',json={'replace':False});self.assertEqual(r.status_code,200,r.text)
        self.assertTrue((self.root/'ocean'/'agent_brief_generated.json').exists())
        self.assertEqual(self.client.post(self.base+'/export',json={}).status_code,409)
        self.assertEqual(self.client.post(self.base+'/export',json={'replace':True}).status_code,200)

    def test_reject_duplicate_before_api_spend(self):
        self.generate()
        with patch('ai_video_editor.api.app.openai_generate') as generate:
            r=self.client.post(self.base+'/generate',json={'requirement':'new','model':'test-model'})
        self.assertEqual(r.status_code,409);generate.assert_not_called()

    def test_edit_revokes_approval_and_validates(self):
        self.generate();self.client.post(self.base+'/approve')
        edited={**copy.deepcopy(SAMPLE),'title':'Edited'}
        r=self.client.put(self.base,json={'content':edited});self.assertEqual(r.status_code,200,r.text)
        self.assertIsNone(r.json()['approved_hash']);self.assertEqual(r.json()['status'],'AWAITING_SCRIPT_APPROVAL')
        self.assertEqual(self.client.post(self.base+'/export',json={}).status_code,409)
        edited['scenes'][0]['type']='invalid'
        self.assertEqual(self.client.put(self.base,json={'content':edited}).status_code,422)
        self.assertEqual(self.client.post(self.base+'/approve').status_code,200)

    def test_existing_scene_plan_is_archived_when_script_changes(self):
        self.generate()
        folder = self.root / 'ocean'
        (folder / 'agent_scene_plan.json').write_text('{}', encoding='utf-8')
        (folder / 'editing_plan.json').write_text('{}', encoding='utf-8')
        (folder / 'output').mkdir(exist_ok=True)
        (folder / 'output' / 'final.mp4').write_bytes(b'previous-render')
        edited = {**copy.deepcopy(SAMPLE), 'title': 'Updated ocean'}
        response = self.client.put(self.base, json={'content': edited})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((folder / 'agent_scene_plan.json').exists())
        self.assertFalse((folder / 'editing_plan.json').exists())
        self.assertTrue((folder / 'output' / 'final.mp4').exists())
        self.assertTrue((folder / 'script_revision_pending.json').exists())
        self.assertEqual(self.client.get('/api/projects/ocean').json()['has_output'], False)
        self.assertEqual(self.client.get('/api/projects/ocean/render/output').status_code, 409)
        self.assertTrue(list((folder / 'history').rglob('agent_scene_plan.json')))
        self.assertEqual(self.client.post(self.base + '/approve').status_code, 200)

    def test_replacing_script_after_scene_plan_does_not_block_api(self):
        self.generate()
        (self.root/'ocean'/'agent_scene_plan.json').write_text('{}', encoding='utf-8')
        with patch('ai_video_editor.api.app.openai_generate', return_value=(copy.deepcopy(SAMPLE), USAGE)) as generate:
            response = self.client.post(self.base + '/generate', json={'requirement': 'new', 'replace': True})
        self.assertEqual(response.status_code, 201, response.text)
        generate.assert_called_once()
        self.assertFalse((self.root/'ocean'/'agent_scene_plan.json').exists())

    def test_reject_cross_site_and_bad_project_id(self):
        r=self.client.post(self.base+'/approve',headers={'Origin':'https://evil.example'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.client.get('/api/projects/nope/script').status_code,404)
        self.assertEqual(self.client.get('/api/projects/%2E%2E/script').status_code,400)

if __name__=='__main__':unittest.main()
