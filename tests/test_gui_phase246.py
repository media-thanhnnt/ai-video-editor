"""Phase 2.4.6: provider credentials, routing, batch quota and UI regressions."""
import copy
import tempfile
import time
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
from ai_video_editor.project import ProjectManager
from ai_video_editor.agent.script_generator import create_script_draft, approve_script

DEFAULT=Path(__file__).resolve().parents[1]/'config/default_project.json'
SCRIPT={'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':10,
        'summary':'Ocean','script':'First. Second.',
        'scenes':[{'scene_id':str(i).zfill(3),'type':'image','narration':f'Voice {i}',
                   'prompt':f'Image {i}','estimated_duration_seconds':5} for i in (1,2)]}

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class RoutingGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'projects'
        self.manager=ProjectManager(self.root,DEFAULT)
        self.project=self.manager.create_project('ocean','Ocean')
        self.client=TestClient(create_app(testing=True, projects_dir=self.root,default_config=DEFAULT))
        self.base='/api/projects/ocean'

    def test_settings_never_exposes_keys_and_checks_allowlist(self):
        r=self.client.get('/api/provider-settings');self.assertEqual(r.status_code,200)
        self.assertNotIn('secret',r.text.lower())
        self.assertIn('gemini',r.json()['providers'])
        self.assertIn('openai',r.json()['providers'])
        self.assertEqual(self.client.get('/api-settings').status_code,200)
        self.assertIn('Save Key',self.client.get('/api-settings').text)
        r=self.client.put('/api/provider-settings',json={'choices':{'image':{'provider':'gemini','model':'gpt-image-2'}}})
        self.assertEqual(r.status_code,422)
        r=self.client.put('/api/provider-settings',json={'choices':{'image':{'provider':'gemini','model':'gemini-2.5-flash-image'}}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['choices']['audio']['provider'],'openai')
        self.assertEqual(self.client.get('/api/provider-settings').json()['choices']['image']['provider'],'gemini')

    def test_keys_are_sent_to_keyring_not_api_responses(self):
        with patch('ai_video_editor.api.provider_settings.keyring_lib') as store:
            r=self.client.put('/api/provider-settings/keys/openai',json={'key':'sk-private-testing-key'})
            self.assertEqual(r.status_code,200,r.text)
            store.return_value.set_password.assert_called_once_with('ai-video-editor-local','openai','sk-private-testing-key')
            self.assertNotIn('sk-private',r.text)
        self.assertEqual(self.client.put('/api/provider-settings/keys/malicious',json={'key':'sk-private-testing-key'}).status_code,422)
        self.assertEqual(self.client.put('/api/provider-settings',json={'choices':{'script':{'provider':'rogue','model':'x'}}}).status_code,422)

    def test_gemini_script_route_uses_shared_validation(self):
        self.client.put('/api/provider-settings',json={'choices':{'script':{'provider':'gemini','model':'gemini-2.5-flash'}}})
        with patch('ai_video_editor.api.app.gemini_text',return_value=(copy.deepcopy(SCRIPT),{'provider':'gemini','model':'gemini-2.5-flash','total_tokens':42})) as gen:
            r=self.client.post(self.base+'/script/generate',json={'requirement':'Ocean','model':'ignored-client-model'})
            self.assertEqual(r.status_code,201,r.text)
            gen.assert_called_once_with('script','Ocean','gemini-2.5-flash')
            self.assertEqual(r.json()['usage']['provider'],'gemini')
        self.assertEqual(self.client.post(self.base+'/script/approve').status_code,200)

    def test_batch_generation_prechecks_quota_and_selected_scenes(self):
        create_script_draft(self.project,copy.deepcopy(SCRIPT),source='manual')
        approve_script(self.project)
        self.client.put(self.base+'/assets/limits',json={'image':1,'audio':10})
        with patch('ai_video_editor.api.app.get_key',return_value='fake-key'), patch('ai_video_editor.api.app.generate_openai_asset') as paid:
            denied=self.client.post(self.base+'/assets/batch-job',json={'scene_ids':['001','002'],'kind':'image','confirmed':True})
            self.assertEqual(denied.status_code,429,denied.text)
            paid.assert_not_called()
        self.assertEqual(self.client.get(self.base+'/assets/status').json()['api_usage']['used']['image'],0)
        with patch('ai_video_editor.api.app.get_key',return_value='fake-key'),patch('ai_video_editor.api.app.generate_openai_asset',return_value=(b'\x89PNG\r\n\x1a\nmock','.png','gpt-image-2')):
            started=self.client.post(self.base+'/assets/batch-job',json={'scene_ids':['002'],'kind':'image','confirmed':True})
            self.assertEqual(started.status_code,202,started.text)
            for _ in range(100):
                job=self.client.get(self.base+'/jobs/'+started.json()['job_id']).json()
                if job['status'] in ('succeeded','failed'):break
                time.sleep(.01)
            self.assertEqual(job['status'],'succeeded',job)
            self.assertEqual(len(job['result']['results']),1)
            self.assertEqual(job['result']['results'][0]['scene_id'],'002')
            self.assertEqual(job['result']['results'][0]['status'],'succeeded')
            self.assertEqual(self.client.get(self.base+'/assets/status').json()['api_usage']['used']['image'],1)

    def test_gemini_image_batch_never_calls_openai(self):
        create_script_draft(self.project,copy.deepcopy(SCRIPT),source='manual')
        approve_script(self.project)
        self.client.put('/api/provider-settings',json={'choices':{'image':{
            'provider':'gemini','model':'gemini-2.5-flash-image'}}})
        with patch('ai_video_editor.api.app.get_key',return_value='fake-gemini-key'), \
             patch('ai_video_editor.api.app.gemini_asset',return_value=(b'\x89PNG\r\n\x1a\nmock','.png','gemini-2.5-flash-image')) as gemini, \
             patch('ai_video_editor.api.app.generate_openai_asset') as openai:
            r=self.client.post(self.base+'/assets/batch-job',json={'scene_ids':['001'],'kind':'image','confirmed':True})
            self.assertEqual(r.status_code,202,r.text)
            for _ in range(100):
                job=self.client.get(self.base+'/jobs/'+r.json()['job_id']).json()
                if job['status'] in ('succeeded','failed'):break
                time.sleep(.01)
            self.assertEqual(job['status'],'succeeded',job)
            gemini.assert_called_once()
            openai.assert_not_called()
            from ai_video_editor.project import read_json
            created=read_json(self.project.folder/'generated_assets.json')['assets']
            self.assertEqual(created[0]['provider'],'gemini')

    def test_assets_page_scene_selection_and_api_summary(self):
        text=self.client.get('/asset-workspace').text
        self.assertLess(text.find('2 · Tình trạng API'),text.find('4 · Danh sách prompt'))
        self.assertLess(text.find('3 · Giới hạn API'),text.find('4 · Danh sách prompt'))
        for fragment in ('selectall','batchImage','batchAudio','scene-panel','id="detail"','/api-settings'):
            self.assertIn(fragment,text)
        script=self.client.get('/script-editor').text
        self.assertIn('3 · Thông tin chung của Script',script)
        self.assertIn('4 · Scenes',script)
        self.assertIn('editor-meta',script)
        self.assertIn('editor-scenes',script)
        self.assertIn('scene-nav',script)
        self.assertIn('Continue to Scene Planner',script)

if __name__=='__main__':unittest.main()
