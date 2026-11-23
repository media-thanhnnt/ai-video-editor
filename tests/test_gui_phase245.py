"""Phase 2.4.5 regression: binding, job status, render approval and local request budgets."""
import copy
import json
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
from ai_video_editor.api.workflow import prepare_manifest, bind_generated
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.agent.asset_workflow import save_generated_asset
from ai_video_editor.project import ProjectManager, ValidationError

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':8,
          'summary':'Ocean','script':'Ocean',
          'scenes':[{'scene_id':'001','type':'image','narration':'The ocean','prompt':'Ocean image','estimated_duration_seconds':8}]}

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class GuiRenderTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.pm=ProjectManager(Path(temporary.name)/'projects', DEFAULT)
        self.project=self.pm.create_project('ocean','Ocean')
        create_script_draft(self.project, copy.deepcopy(SCRIPT), source='manual')
        approve_script(self.project)
        self.client=TestClient(create_app(testing=True, projects_dir=self.pm.projects_dir, default_config=DEFAULT))
        self.base='/api/projects/ocean'
        key_patch=patch('ai_video_editor.api.app.get_key',return_value='test-api-key')
        key_patch.start();self.addCleanup(key_patch.stop)

    def test_render_ui_and_approval_gates(self):
        self.assertIn('Render Studio', self.client.get('/render-studio').text)
        self.assertEqual(self.client.post(self.base+'/render/start',json={'confirmed':False}).status_code,400)
        self.assertEqual(self.client.post(self.base+'/render/start',json={'confirmed':True}).status_code,422)
        m=self.client.post(self.base+'/assets/manifest')
        self.assertEqual(m.status_code,200,m.text)
        self.assertEqual(self.client.post(self.base+'/render/prepare',json={'approve_manifest':True}).status_code,422)

    def test_autobind_matching_generated_assets_and_render_approval(self):
        self.client.post(self.base+'/assets/manifest')
        save_generated_asset(self.project,'001','image',b'\x89PNG\r\n\x1a\nmock', '.png','gpt-image-2',source_prompt='ocean')
        save_generated_asset(self.project,'001','audio',b'ID3mock', '.mp3','gpt-4o-mini-tts',source_prompt='The ocean')
        out=self.client.post(self.base+'/assets/auto-bind')
        self.assertEqual(out.status_code,200,out.text)
        self.assertEqual(len(out.json()['bound']),2)
        self.assertEqual(out.json()['missing'],[])
        status=self.client.get(self.base+'/assets/status').json()
        self.assertEqual(status['manifest']['status'],'READY_FOR_REVIEW')
        self.assertEqual(self.client.post(self.base+'/render/prepare',json={'approve_manifest':True}).status_code,200)
        self.assertEqual(self.client.post(self.base+'/render/start',json={'confirmed':True}).status_code,422)
        self.assertEqual(self.client.post(self.base+'/render/approve-plan').status_code,200)
        with patch('ai_video_editor.api.app.render_cli',return_value={'status':'finished'}) as render:
            started=self.client.post(self.base+'/render/start',json={'confirmed':True})
            self.assertEqual(started.status_code,202,started.text)
            jid=started.json()['job_id']
            for _ in range(100):
                state=self.client.get(self.base+'/jobs/'+jid).json()
                if state['status'] in ('succeeded','failed'):
                    break
                time.sleep(.01)
            self.assertEqual(state['status'],'succeeded',state)
            render.assert_called_once()
        self.assertEqual(self.client.get('/api/projects/another/jobs/'+jid).status_code,404)

    def test_upload_and_bind_requires_correct_type(self):
        self.client.post(self.base+'/assets/manifest')
        file={'file':('image.png',b'\x89PNG\r\n\x1a\nmock','image/png')}
        r=self.client.post(self.base+'/assets/upload',data={'scene_id':'001','kind':'image'},files=file)
        self.assertEqual(r.status_code,201,r.text)
        self.assertTrue(r.json()['file'].startswith('assets/images/'))
        r=self.client.post(self.base+'/assets/upload',data={'scene_id':'001','kind':'video'},files={'file':('v.mp4',b'123','video/mp4')})
        self.assertEqual(r.status_code,422)

    def test_jobs_are_async_and_repeated_calls_blocked(self):
        import threading
        gate=threading.Event()
        def fake(*args,**kwargs):
            gate.wait(timeout=2)
            return b'ID3mock','.mp3','gpt-4o-mini-tts'
        with patch('ai_video_editor.api.app.generate_openai_asset',side_effect=fake):
            request={'scene_id':'001','kind':'audio','confirmed':True}
            first=self.client.post(self.base+'/assets/generate-job',json=request)
            self.assertEqual(first.status_code,202,first.text)
            self.assertEqual(self.client.post(self.base+'/assets/generate-job',json=request).status_code,409)
            limit=self.client.get(self.base+'/assets/status').json()['api_usage']
            self.assertEqual(limit['used']['audio'],1)
            self.assertEqual(limit['limits']['audio'],10)
            gate.set()
            for _ in range(100):
                state=self.client.get(self.base+'/jobs/'+first.json()['job_id']).json()
                if state['status']=='succeeded': break
                time.sleep(.01)
            self.assertEqual(state['status'],'succeeded',state)
            self.assertTrue(state['result']['auto_bound'])
            self.assertEqual(self.client.get(self.base+'/assets/status').json()['manifest']['scenes'][0]['voice_file'],state['result']['file'])

    def test_local_limit_settings(self):
        user_limit=self.client.put(self.base+'/assets/limits',json={'image':21,'audio':5})
        self.assertEqual(user_limit.status_code,200,user_limit.text)
        self.assertEqual(user_limit.json()['limits']['image'],21)
        updated=self.client.put(self.base+'/assets/limits',json={'image':0,'audio':2})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['limits']['image'],0)
        with patch('ai_video_editor.api.app.generate_openai_asset') as paid:
            denied=self.client.post(self.base+'/assets/generate-job',json={
                'scene_id':'001','kind':'image','confirmed':True})
            self.assertEqual(denied.status_code,429,denied.text)
            paid.assert_not_called()
        state=self.client.get(self.base+'/assets/status').json()['api_usage']
        self.assertEqual(state['used']['image'],0)
        self.assertEqual(state['remaining']['audio'],2)

    def test_paid_calls_have_local_daily_ceiling(self):
        with patch('ai_video_editor.api.app.generate_openai_asset',return_value=(b'ID3mock','.mp3','gpt-4o-mini-tts')):
            req={'scene_id':'001','kind':'audio','confirmed':True}
            for _ in range(10):
                self.assertEqual(self.client.post(self.base+'/assets/generate',json=req).status_code,201)
            self.assertEqual(self.client.post(self.base+'/assets/generate',json=req).status_code,429)
        limit=self.client.get(self.base+'/assets/status').json()['api_usage']
        self.assertEqual(limit['used']['audio'],10)
        self.assertEqual(limit['limits']['image'],5)

if __name__ == '__main__': unittest.main()
