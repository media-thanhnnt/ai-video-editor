"""Opt-in asset workflows: no hidden paid API calls, prompt pack fidelity, safety."""
import base64
import copy
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
    TestClient = None
    create_app = None
    HAS_UI = False
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.agent.asset_workflow import make_prompt_pack, render_prompt_markdown, generate_openai_asset
from ai_video_editor.project import ProjectManager, ValidationError

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':20,
          'summary':'Ocean','script':'Blue ocean. Coral reef.',
          'scenes':[{'scene_id':'001','type':'image','narration':'Blue ocean','prompt':'Aerial ocean image','estimated_duration_seconds':10},
                    {'scene_id':'002','type':'video_clip','narration':'Coral reef','prompt':'Video coral reef','estimated_duration_seconds':10}]}

@unittest.skipUnless(HAS_UI, 'GUI deps missing: pip install -e ".[ui,test]"')
class OptInAssetGuiTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.manager=ProjectManager(Path(tmp.name)/'projects',DEFAULT)
        self.project=self.manager.create_project('ocean','Ocean')
        create_script_draft(self.project, copy.deepcopy(SCRIPT), source='manual')
        self.client=TestClient(create_app(testing=True, projects_dir=self.manager.projects_dir, default_config=DEFAULT))
        self.base='/api/projects/ocean'
        key_patch=patch('ai_video_editor.api.app.get_key',return_value='test-api-key')
        key_patch.start();self.addCleanup(key_patch.stop)

    def test_prompt_export_without_approval_or_external_calls(self):
        with patch('ai_video_editor.api.app.generate_openai_asset') as paid:
            data=self.client.get(self.base+'/asset-prompts').json()
            self.assertEqual(len(data['scenes']),2)
            self.assertEqual(data['scenes'][0]['image_prompt'],'Aerial ocean image')
            self.assertEqual(data['scenes'][1]['video_prompt'],'Video coral reef')
            self.assertEqual(data['scenes'][1]['audio_prompt'],'Coral reef')
            markdown=self.client.get(self.base+'/asset-prompts.md')
            self.assertEqual(markdown.status_code,200)
            self.assertIn('Video coral reef',markdown.text)
            self.assertIn('Audio / Voice narration',markdown.text)
            paid.assert_not_called()

    def test_optional_generation_requires_explicit_consent(self):
        with patch('ai_video_editor.api.app.generate_openai_asset') as paid:
            result=self.client.post(self.base+'/assets/generate',json={'scene_id':'001','kind':'image'})
            self.assertEqual(result.status_code,400)
            result=self.client.post(self.base+'/assets/generate',json={'scene_id':'fake','kind':'image','confirmed':True})
            self.assertEqual(result.status_code,404)
            result=self.client.post(self.base+'/assets/generate',json={'scene_id':'001','kind':'video','confirmed':True})
            self.assertEqual(result.status_code,422)
            paid.assert_not_called()

    def test_generate_image_and_audio_saves_without_overwrite_or_autobind(self):
        png=b'\x89PNG\r\n\x1a\n' + b'PNG-mock'
        mp3=b'ID3' + b'MP3-mock'
        with patch('ai_video_editor.api.app.generate_openai_asset',side_effect=[(png,'.png','gpt-image-2'),(mp3,'.mp3','gpt-4o-mini-tts')]) as paid:
            a=self.client.post(self.base+'/assets/generate',json={'scene_id':'001','kind':'image','confirmed':True})
            b=self.client.post(self.base+'/assets/generate',json={'scene_id':'002','kind':'audio','confirmed':True})
        self.assertEqual(a.status_code,201,a.text)
        self.assertEqual(b.status_code,201,b.text)
        self.assertFalse(a.json()['auto_bound'])
        self.assertTrue((self.project.folder/a.json()['file']).is_file())
        self.assertTrue((self.project.folder/b.json()['file']).is_file())
        self.assertEqual(paid.call_count,2)
        self.assertEqual(len(json.loads((self.project.folder/'generated_assets.json').read_text())['assets']),2)
        self.assertFalse((self.project.folder/'agent_manifest.json').exists())

    def test_safeguards_cross_origin_and_invalid_project(self):
        self.assertEqual(self.client.post(self.base+'/assets/generate',json={'scene_id':'001','kind':'image','confirmed':True},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/projects/doesnotexist/asset-prompts').status_code,404)
        self.assertEqual(self.client.get('/asset-workspace').status_code,200)
        self.assertIn('Export prompts .md',self.client.get('/asset-workspace').text)
        self.assertIn('asset-workspace',self.client.get('/script-editor').text)

    def test_image_model_validation_happens_before_http(self):
        with self.assertRaises(ValidationError):
            generate_openai_asset('image','a fish',model='gpt-6-luna',api_key='fake')
        with self.assertRaises(ValidationError):
            generate_openai_asset('video','a fish',api_key='fake')

    def test_api_mock_image_and_speech_payload(self):
        png=b'\x89PNG\r\n\x1a\n' + b'data'
        with patch('ai_video_editor.agent.asset_workflow._post_json',return_value=json.dumps({'data':[{'b64_json':base64.b64encode(png).decode()}]}).encode()) as fake:
            data,ext,model=generate_openai_asset('image','A fish',api_key='fake')
            self.assertEqual((data,ext,model),(png,'.png','gpt-image-2'))
            self.assertEqual(fake.call_args.args[0],'images/generations')
        with patch('ai_video_editor.agent.asset_workflow._post_json',return_value=b'ID3abc') as fake:
            data,ext,model=generate_openai_asset('audio','Xin chao',api_key='fake')
            self.assertEqual(ext,'.mp3')
            self.assertEqual(fake.call_args.args[0],'audio/speech')

if __name__ == '__main__':unittest.main()
