"""Phase 2.4.9: persistent, editable provider/model catalog."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.api.provider_settings import (add_model, add_provider, catalog_path,
    delete_model, delete_provider, load_registry, load_choices, save_choices, public_settings)
from ai_video_editor.project import ValidationError

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_GUI = True
except (ImportError, RuntimeError):
    HAS_GUI = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'


class ProviderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name) / 'projects'
        self.projects.mkdir()

    def test_initial_registry_and_keys_not_in_file(self):
        registry = load_registry(self.projects)
        self.assertIn('openai', registry['providers'])
        self.assertIn('gemini', registry['providers'])
        self.assertFalse(catalog_path(self.projects).exists())

    def test_add_provider_and_models_persist(self):
        add_provider(self.projects, provider_id='vendor_x', label='Vendor X', base_url='https://api.vendor.example/v1')
        add_model(self.projects, provider_id='vendor_x', task='script', model='video-text-v3')
        add_model(self.projects, provider_id='vendor_x', task='image', model='image-custom-1')
        choices = save_choices(self.projects, {'script': {'provider': 'vendor_x', 'model': 'video-text-v3'}})
        self.assertEqual(choices['script']['provider'], 'vendor_x')
        self.assertEqual(load_choices(self.projects)['script']['model'], 'video-text-v3')
        raw = json.loads(catalog_path(self.projects).read_text())
        self.assertEqual(raw['providers']['vendor_x']['base_url'], 'https://api.vendor.example/v1')
        self.assertNotIn('api_key', raw['providers']['vendor_x'])
        with patch('ai_video_editor.api.provider_settings.get_key', return_value=None):
            public = public_settings(self.projects)
        self.assertIn('vendor_x', public['providers'])
        self.assertIn('video-text-v3', public['catalog']['script']['vendor_x'])
        self.assertNotIn('key', public['providers']['vendor_x'])

    def test_reject_bad_url_and_builtin_deletion(self):
        for url in ('http://api.vendor.example/v1', 'https://127.0.0.1/v1',
                    'https://localhost/v1', 'https://alice:password@api.vendor.example/v1',
                    'https://api.vendor.example/v1?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                add_provider(self.projects, provider_id='unsafe', label='Unsafe', base_url=url)
        with self.assertRaises(ValidationError):
            delete_provider(self.projects, 'openai')

    def test_duplicate_and_model_remove_repair_choice(self):
        add_model(self.projects, provider_id='openai', task='script', model='new-text-model')
        with self.assertRaises(ValidationError):
            add_model(self.projects, provider_id='openai', task='script', model='new-text-model')
        save_choices(self.projects, {'script': {'provider': 'openai', 'model': 'new-text-model'}})
        delete_model(self.projects, provider_id='openai', task='script', model='new-text-model')
        self.assertNotEqual(load_choices(self.projects)['script']['model'], 'new-text-model')

    def test_custom_provider_deletion_reverts_selection(self):
        add_provider(self.projects, provider_id='vendor_x', label='Vendor X', base_url='https://vendor.example/v1')
        add_model(self.projects, provider_id='vendor_x', task='audio', model='voice-v1')
        save_choices(self.projects, {'audio': {'provider': 'vendor_x', 'model': 'voice-v1'}})
        with patch('ai_video_editor.api.provider_settings.keyring_lib') as credential_store:
            credential_store.return_value.get_password.return_value = None
            delete_provider(self.projects, 'vendor_x')
        self.assertEqual(load_choices(self.projects)['audio']['provider'], 'openai')


@unittest.skipUnless(HAS_GUI, 'Missing optional UI dependencies')
class ProviderCatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name) / 'projects'
        self.projects.mkdir()
        self.client = TestClient(create_app(testing=True, projects_dir=self.projects, default_config=DEFAULT))

    def test_add_provider_model_and_select(self):
        payload = {'provider_id': 'local_vendor', 'label': 'Vendor Local',
                   'base_url': 'https://llm.vendor.example/v1'}
        self.assertEqual(self.client.post('/api/provider-settings/providers', json=payload).status_code, 201)
        self.assertEqual(self.client.post('/api/provider-settings/models', json={
            'provider_id': 'local_vendor', 'task': 'scene_plan', 'model': 'scene-v1'}).status_code, 201)
        response = self.client.put('/api/provider-settings', json={'choices': {'scene_plan': {
            'provider': 'local_vendor', 'model': 'scene-v1'}}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['choices']['scene_plan']['provider'], 'local_vendor')
        self.assertTrue(catalog_path(self.projects).exists())

    def test_custom_provider_dispatches_to_its_endpoint(self):
        from unittest.mock import patch
        self.client.post('/api/provider-settings/providers', json={
            'provider_id':'vendorz','label':'Vendor Z','base_url':'https://api.vendorz.example/v1'})
        self.client.post('/api/provider-settings/models', json={
            'provider_id':'vendorz','task':'script','model':'writer-v1'})
        self.client.put('/api/provider-settings', json={'choices':{
            'script':{'provider':'vendorz','model':'writer-v1'}}})
        with patch('ai_video_editor.api.app.openai_generate') as generate, \
             patch('ai_video_editor.api.app.get_key', return_value='test-key'):
            generate.return_value = ({'title':'Demo','language':'vi','style':'documentary',
                'target_duration_seconds':10,'summary':'Demo','script':'An ocean.',
                'scenes':[{'scene_id':'001','type':'image','narration':'Ocean',
                           'prompt':'Blue ocean','estimated_duration_seconds':10}]},
                {'provider':'openai','model':'writer-v1','total_tokens':12})
            self.client.post('/api/projects',json={'project_id':'vendor_demo','title':'Vendor Demo'})
            result=self.client.post('/api/projects/vendor_demo/script/generate', json={
                'requirement':'Ocean documentary'})
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()['usage']['provider'],'vendorz')
            generate.assert_called_once_with('Ocean documentary',model='writer-v1',
                api_key='test-key',base_url='https://api.vendorz.example/v1')

    def test_reject_unsafe_and_video_model(self):
        self.assertEqual(self.client.post('/api/provider-settings/providers', json={
            'provider_id': 'unsafe', 'label': 'Unsafe', 'base_url': 'http://localhost:8080/v1'}).status_code, 422)
        self.assertEqual(self.client.post('/api/provider-settings/models', json={
            'provider_id': 'openai', 'task': 'video', 'model': 'video-v1'}).status_code, 422)
        self.assertEqual(self.client.delete('/api/provider-settings/providers/openai').status_code, 422)


if __name__ == '__main__':
    unittest.main()
