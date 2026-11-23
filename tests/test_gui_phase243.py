"""Phase 2.4.3 API & UI, real scene business logic and mocked paid OpenAI call."""
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
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.project import ProjectManager

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':20,
          'summary':'Ocean','script':'Blue ocean. Coral reef.',
          'scenes':[{'scene_id':'001','type':'image','narration':'Blue ocean',
                     'prompt':'Ocean','estimated_duration_seconds':10},
                    {'scene_id':'002','type':'video_clip','narration':'Coral reef',
                     'prompt':'Reef','estimated_duration_seconds':10}]}
PLAN = {'style':'documentary','scenes':[
    {'scene_id':'001','media_type':'image','asset_prompt_en':'Blue ocean aerial shot',
     'estimated_duration_seconds':10,'motion':'pan_right','motion_intensity':'subtle',
     'transition_to_next':'dissolve','transition_length':'short','reason':'Establish ocean'},
    {'scene_id':'002','media_type':'video_clip','asset_prompt_en':'Slow underwater reef shot',
     'estimated_duration_seconds':10,'motion':'static','motion_intensity':'subtle',
     'transition_to_next':'none','transition_length':None,'reason':'Natural video motion'}]}
USAGE = {'provider':'openai','model':'test-model','input_tokens':12,'output_tokens':20,'total_tokens':32,'estimated_cost_usd':None}

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class SceneGuiTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'projects'
        manager=ProjectManager(self.root,DEFAULT)
        self.project=manager.create_project('ocean','Ocean')
        create_script_draft(self.project,copy.deepcopy(SCRIPT),source='manual')
        approve_script(self.project)
        self.client=TestClient(create_app(testing=True, projects_dir=self.root,default_config=DEFAULT))
        secret_patch = patch('ai_video_editor.api.app._provider_key', return_value='mock-provider-key')
        secret_patch.start(); self.addCleanup(secret_patch.stop)
        self.base='/api/projects/ocean/scene-plan'

    def generate(self):
        with patch('ai_video_editor.api.app.generate_openai_scene_plan',return_value=(copy.deepcopy(PLAN),USAGE)) as fn:
            r=self.client.post(self.base+'/generate',json={'model':'test-model'})
            fn.assert_called_once_with(SCRIPT,model='gpt-6-luna',api_key='mock-provider-key')
        self.assertEqual(r.status_code,201,r.text)
        return r

    def test_page_navigation_and_script_export_cta(self):
        self.assertEqual(self.client.get('/scene-planner').status_code,200)
        self.assertIn('Generate Scene Plan',self.client.get('/scene-planner').text)
        script=self.client.get('/script-editor').text
        self.assertIn('Continue to Scene Planner',script)
        self.assertIn('showContinue()',script)
        self.assertIn('/static/sidebar.js',self.client.get('/').text)
        self.assertIn('/scene-planner',self.client.get('/static/sidebar.js').text)
        self.assertEqual(self.client.get(self.base).status_code,404)
        self.assertEqual(self.client.get('/api/health').json()['phase'],'2.5.1.3')

    def test_generate_approve_export_without_overwriting(self):
        self.generate()
        r=self.client.get(self.base);self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['status'],'AWAITING_SCENE_PLAN_APPROVAL')
        self.assertEqual(r.json()['usage']['total_tokens'],32)
        self.assertEqual(self.client.post(self.base+'/export',json={}).status_code,409)
        r=self.client.post(self.base+'/approve');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'SCENE_PLAN_APPROVED')
        r=self.client.post(self.base+'/export',json={});self.assertEqual(r.status_code,200,r.text)
        self.assertTrue((self.project.folder/'agent_scene_brief.json').exists())
        self.assertEqual(self.client.post(self.base+'/export',json={}).status_code,409)
        self.assertEqual(self.client.post(self.base+'/export',json={'replace':True}).status_code,200)

    def test_before_paid_call_guard(self):
        self.generate()
        with patch('ai_video_editor.api.app.generate_openai_scene_plan') as mocked:
            r=self.client.post(self.base+'/generate',json={'model':'test-model'})
            self.assertEqual(r.status_code,409);mocked.assert_not_called()
        self.client.post(self.base+'/approve')
        self.client.post(self.base+'/export',json={})
        with patch('ai_video_editor.api.app.generate_openai_scene_plan') as mocked:
            r=self.client.post(self.base+'/generate',json={'replace':True,'model':'test-model'})
            self.assertEqual(r.status_code,409);mocked.assert_not_called()

    def test_edit_revokes_approval_and_rejects_invalid(self):
        self.generate();self.client.post(self.base+'/approve')
        updated=copy.deepcopy(PLAN);updated['scenes'][0]['asset_prompt_en']='Ocean with sunbeams'
        r=self.client.put(self.base,json={'content':updated});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'AWAITING_SCENE_PLAN_APPROVAL')
        self.assertIsNone(r.json()['approved_hash'])
        self.assertNotEqual(self.client.post(self.base+'/export',json={}).status_code,200)
        bad=copy.deepcopy(updated);bad['scenes'][1]['motion']='zoom_in'
        self.assertEqual(self.client.put(self.base,json={'content':bad}).status_code,422)
        self.assertEqual(self.client.post(self.base+'/approve').status_code,200)

    def test_script_requires_approval_before_paid_call(self):
        script=self.project.folder/'agent_script_draft.json'
        from ai_video_editor.project import read_json,atomic_write_json
        data=read_json(script);data['status']='AWAITING_SCRIPT_APPROVAL';atomic_write_json(script,data)
        with patch('ai_video_editor.api.app.generate_openai_scene_plan') as mocked:
            r=self.client.post(self.base+'/generate',json={'model':'test-model'})
            self.assertEqual(r.status_code,409);mocked.assert_not_called()

    def test_origin_protection_and_path_validation(self):
        self.assertEqual(self.client.post(self.base+'/approve',headers={'Origin':'https://bad.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/projects/nope/scene-plan').status_code,404)
        self.assertEqual(self.client.get('/api/projects/%2E%2E/scene-plan').status_code,400)

if __name__ == '__main__':unittest.main()
