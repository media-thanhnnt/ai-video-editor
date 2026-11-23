"""Regression tests for the Phase 2.4.10 paid-API, compile and security fixes."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.project import ProjectManager, ValidationError
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.agent.asset_workflow import generate_openai_asset, save_generated_asset
from ai_video_editor.api.gemini_provider import parts_of, usage_of, generate_asset as gemini_asset
from ai_video_editor.planner import get_default_editing_plan, validate_editing_plan
from ai_video_editor.api.workflow import prepare_manifest

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI = True
except (ImportError, RuntimeError):
    HAS_UI = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':10,
          'summary':'Ocean','script':'Đại dương rộng lớn',
          'scenes':[{'scene_id':'001','type':'image','narration':'Đại dương rộng lớn',
                     'prompt':'English cinematic aerial ocean image prompt',
                     'estimated_duration_seconds':10}]}


def done(client, base, response):
    assert response.status_code == 202, response.text
    for _ in range(200):
        state = client.get(base + '/jobs/' + response.json()['job_id']).json()
        if state['status'] in ('succeeded','failed'):
            return state
        time.sleep(.01)
    raise AssertionError('Job did not finish')


class ProviderErrors(unittest.TestCase):
    def test_openai_image_broken_json(self):
        with patch('ai_video_editor.agent.asset_workflow._post_json', return_value=b'<html>'):
            with self.assertRaises(ValidationError):
                generate_openai_asset('image', 'ocean', api_key='fake-key')
        with patch('ai_video_editor.agent.asset_workflow._post_json', return_value=b'{"data":[{"b64_json":"broken%%"}]}'):
            with self.assertRaises(ValidationError):
                generate_openai_asset('image', 'ocean', api_key='fake-key')

    def test_gemini_broken_data(self):
        from ai_video_editor.api import gemini_provider as gp
        with patch.object(gp, 'request_model', return_value={'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':'%%%'}}]}}]}):
            with self.assertRaises(ValidationError):
                gemini_asset('image', 'ocean', 'gemini-2.5-flash-image')
        with self.assertRaises(ValidationError):
            parts_of({'candidates':[{'content':None}]})
        with self.assertRaises(ValidationError):
            usage_of({'usageMetadata':{'promptTokenCount':'not-a-number'}}, 'model')


@unittest.skipUnless(HAS_UI, 'GUI deps missing')
class HotfixGui(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.projects=Path(tmp.name)/'projects'
        self.manager=ProjectManager(self.projects, DEFAULT)
        self.project=self.manager.create_project('ocean', 'Ocean')
        create_script_draft(self.project, copy.deepcopy(SCRIPT), source='manual')
        approve_script(self.project)
        self.client=TestClient(create_app(testing=True, projects_dir=self.projects, default_config=DEFAULT))
        self.base='/api/projects/ocean'

    def test_missing_key_does_not_consume_quota_sync_job_batch(self):
        with patch('ai_video_editor.api.app.get_key',return_value=None):
            for endpoint, payload in [('/assets/generate',{'scene_id':'001','kind':'image','confirmed':True}),
                                      ('/assets/generate-job',{'scene_id':'001','kind':'audio','confirmed':True}),
                                      ('/assets/batch-job',{'scene_ids':['001'],'kind':'audio','confirmed':True})]:
                response=self.client.post(self.base+endpoint,json=payload)
                self.assertEqual(response.status_code,422,(endpoint,response.text))
        counts=self.client.get(self.base+'/assets/status').json()['api_usage']['used']
        self.assertEqual(counts,{'image':0,'audio':0})

    def test_voice_single_and_batch_use_narration_not_image_prompt(self):
        with patch('ai_video_editor.api.app.get_key', return_value='fake-key'), \
             patch('ai_video_editor.api.app.generate_openai_asset',return_value=(b'ID3voice','.mp3','tts-model')) as gen:
            one=done(self.client,self.base,self.client.post(self.base+'/assets/generate-job',json={
                'scene_id':'001','kind':'audio','confirmed':True}))
            self.assertEqual(one['status'],'succeeded',one)
            self.assertEqual(gen.call_args.args[:2],('audio','Đại dương rộng lớn'))
            batch=done(self.client,self.base,self.client.post(self.base+'/assets/batch-job',json={
                'scene_ids':['001'],'kind':'audio','confirmed':True}))
            self.assertEqual(batch['status'],'succeeded',batch)
            self.assertEqual(gen.call_args.args[:2],('audio','Đại dương rộng lớn'))
            generated=json.loads((self.project.folder/'generated_assets.json').read_text())
            self.assertTrue(all(row['prompt_sha256'] == __import__('hashlib').sha256('Đại dương rộng lớn'.encode()).hexdigest() for row in generated['assets']))

    def test_m4a_upload_is_rejected_without_leftover(self):
        prepare_manifest(self.project)
        response=self.client.post(self.base+'/assets/upload',data={'scene_id':'001','kind':'audio'},
            files={'file':('wrong.m4a',b'123','audio/mp4')})
        self.assertEqual(response.status_code,422,response.text)
        self.assertFalse(list((self.project.folder/'assets'/'audio').glob('uploaded_*.m4a')))

    def test_invalid_limits_json_returns_422(self):
        (self.project.folder/'api_request_limits.json').write_text('{ not json')
        response=self.client.get(self.base+'/assets/status')
        self.assertEqual(response.status_code,422,response.text)

    def test_invalid_image_api_json_returns_422_not_500(self):
        with patch('ai_video_editor.api.app.get_key',return_value='fake-key'), \
             patch('ai_video_editor.agent.asset_workflow._post_json',return_value=b'<html>'):
            response=self.client.post(self.base+'/assets/generate',json={'scene_id':'001','kind':'image','confirmed':True})
        self.assertEqual(response.status_code,422,response.text)

    def test_rebind_recompile_approval_and_preserve_old_plan(self):
        prepare_manifest(self.project)
        save_generated_asset(self.project,'001','image',b'\x89PNG\r\n\x1a\nimage1','.png','m',source_prompt='ocean')
        save_generated_asset(self.project,'001','audio',b'ID3voice1','.mp3','m',source_prompt='voice')
        bind=self.client.post(self.base+'/assets/auto-bind')
        self.assertEqual(bind.status_code,200,bind.text)
        prep=self.client.post(self.base+'/render/prepare',json={'approve_manifest':True})
        self.assertEqual(prep.status_code,200,prep.text)
        self.assertEqual(prep.json()['manifest_status'],'COMPILED')
        self.assertEqual(self.client.post(self.base+'/render/approve-plan').status_code,200)
        save_generated_asset(self.project,'001','audio',b'ID3voice2','.mp3','m',source_prompt='new voice')
        new_audio=[i for i in json.loads((self.project.folder/'generated_assets.json').read_text())['assets']
                   if i['kind']=='audio'][-1]['path']
        result=self.client.post(self.base+'/assets/bind',json={'scene_id':'001','kind':'voice','file':new_audio})
        self.assertEqual(result.status_code,200,result.text)
        before_backups=set(self.project.folder.glob('editing_plan_backup_*.json'))
        refreshed=self.client.post(self.base+'/render/prepare',json={'approve_manifest':True})
        self.assertEqual(refreshed.status_code,200,refreshed.text)
        self.assertEqual(refreshed.json()['manifest_status'],'COMPILED')
        # Voice replacement leaves editing decisions unchanged; retain approval.
        self.assertTrue(refreshed.json()['plan_approved'])
        self.assertEqual(set(self.project.folder.glob('editing_plan_backup_*.json')),before_backups)
        self.assertEqual(json.loads((self.project.folder/'storyboard.json').read_text())['scenes'][0]['audio'],new_audio)

    def test_production_rejects_testserver_host(self):
        production=TestClient(create_app(projects_dir=self.projects,default_config=DEFAULT))
        self.assertEqual(production.get('/api/projects').status_code,400)
        self.assertEqual(production.get('/api/projects',headers={'Host':'localhost:8765'}).status_code,200)

    def test_gemini_editing_plan_metadata_is_valid(self):
        plan=get_default_editing_plan(self.project)
        plan['plan_meta'].update(source='ai_assisted',provider='gemini',model='gemini-2.5-flash',prompt_version='ai-scene-v2.3.0')
        validate_editing_plan(self.project,plan)


if __name__=='__main__': unittest.main()
