"""Security and data-integrity regressions from src_new_version.zip review."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.project import ProjectManager, ValidationError, atomic_write_json
from ai_video_editor.agent.script_generator import openai_generate, create_script_draft, approve_script
from ai_video_editor.agent.scene_planner import generate_openai_scene_plan
from ai_video_editor.agent.asset_workflow import generate_openai_asset
from ai_video_editor.api.provider_settings import add_provider, add_model, delete_provider, load_registry
from ai_video_editor.api.workflow import prepare_render, prepare_manifest
from ai_video_editor.api.limits import set_limits
from ai_video_editor.cli import main

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    UI_AVAILABLE = True
except (ImportError, RuntimeError):
    TestClient = None
    create_app = None
    UI_AVAILABLE = False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {
    'title': 'Ocean', 'language': 'vi', 'style': 'documentary',
    'target_duration_seconds': 10, 'summary': 'Ocean', 'script': 'Lời dẫn tiếng Việt.',
    'scenes': [{'scene_id': '001', 'type': 'image', 'narration': 'Lời dẫn tiếng Việt.',
                'prompt': 'Beautiful ocean image', 'estimated_duration_seconds': 10}],
}


class ProviderKeyIsolationTests(unittest.TestCase):
    def test_custom_script_api_cannot_fallback_to_openai_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret-openai-value'}):
            with self.assertRaises(ValidationError), patch('ai_video_editor.agent.script_generator.request.urlopen') as call:
                openai_generate('An ocean', base_url='https://api.otherprovider.example/v1')
            call.assert_not_called()

    def test_custom_scene_api_cannot_fallback_to_openai_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret-openai-value'}):
            with self.assertRaises(ValidationError), patch('ai_video_editor.agent.scene_planner.request.urlopen') as call:
                generate_openai_scene_plan(copy.deepcopy(SCRIPT), base_url='https://api.otherprovider.example/v1')
            call.assert_not_called()

    def test_custom_asset_api_cannot_fallback_to_openai_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret-openai-value'}):
            with self.assertRaises(ValidationError), patch('ai_video_editor.agent.asset_workflow.request.urlopen') as call:
                generate_openai_asset('image', 'ocean', base_url='https://api.otherprovider.example/v1', compatible=True)
            call.assert_not_called()

    def test_deleting_provider_clears_credential_before_reusing_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'projects'; root.mkdir()
            add_provider(root, provider_id='vendorkey', label='Vendor', base_url='https://vendor.example/v1')
            with patch('ai_video_editor.api.provider_settings.keyring_lib') as make_store:
                store = make_store.return_value
                store.get_password.return_value = 'secret-value'
                delete_provider(root, 'vendorkey')
                store.delete_password.assert_called_once()
            self.assertNotIn('vendorkey', load_registry(root)['providers'])

    def test_failed_keyring_deletion_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'projects'; root.mkdir()
            add_provider(root, provider_id='vendorkey', label='Vendor', base_url='https://vendor.example/v1')
            with patch('ai_video_editor.api.provider_settings.keyring_lib') as make_store:
                store = make_store.return_value
                store.get_password.return_value = 'old-secret'
                store.delete_password.side_effect = RuntimeError('keyring unavailable')
                with self.assertRaises(ValidationError):
                    delete_provider(root, 'vendorkey')
            self.assertIn('vendorkey', load_registry(root)['providers'])

    def test_builtin_catalog_lists_not_mutated_before_file_exists(self):
        from ai_video_editor.api.provider_settings import CATALOG
        old = list(CATALOG['script']['openai'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'projects'; root.mkdir()
            add_model(root, provider_id='openai', task='script', model='test-custom-safe')
            self.assertEqual(CATALOG['script']['openai'], old)


@unittest.skipUnless(UI_AVAILABLE, 'FastAPI UI dependencies not installed')
class ApiInputHandlingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name) / 'projects'
        self.project = ProjectManager(self.projects, DEFAULT).create_project('demo', 'Demo')
        create_script_draft(self.project, copy.deepcopy(SCRIPT), source='manual')
        approve_script(self.project)
        self.client = TestClient(create_app(projects_dir=self.projects, default_config=DEFAULT, testing=True))
        self.api = '/api/projects/demo'

    def test_invalid_provider_catalog_returns_422(self):
        path = self.projects.parent / 'config' / 'providers.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text('{ not JSON', encoding='utf-8')
        self.assertEqual(self.client.get('/api/provider-settings').status_code, 422)

    def test_missing_audio_narration_batch_returns_422(self):
        data = self.client.get(self.api + '/script').json()
        data['content']['scenes'][0]['narration'] = ''
        # For a video clip empty narration is valid but TTS must not be called.
        data['content']['scenes'][0]['type'] = 'video_clip'
        from ai_video_editor.agent.script_generator import digest
        data['approved_hash'] = digest(data['content'])
        atomic_write_json(self.project.folder / 'agent_script_draft.json', data)
        response = self.client.post(self.api + '/assets/batch-job', json={
            'scene_ids': ['001'], 'kind': 'audio', 'confirmed': True})
        self.assertEqual(response.status_code, 422, response.text)

    def test_invalid_brief_narration_returns_422(self):
        brief = {'title': 'Ocean', 'scenes': [{'scene_id': '001', 'type': 'image',
                 'narration': ['wrong-type'], 'prompt': 'New image'}]}
        response = self.client.post(self.api + '/script/import', json={'content': brief, 'replace': True})
        self.assertEqual(response.status_code, 422, response.text)

    def test_failed_replacement_keeps_previous_editing_plan(self):
        from ai_video_editor.agent.asset_workflow import save_generated_asset
        from ai_video_editor.api.workflow import bind_generated
        prepare_manifest(self.project)
        save_generated_asset(self.project, '001', 'image', b'fake-image', '.png', 'image-model',
                             source_prompt='Beautiful ocean image')
        save_generated_asset(self.project, '001', 'audio', b'fake-voice', '.mp3', 'tts-model',
                             source_prompt='Lời dẫn tiếng Việt.')
        bind_generated(self.project)
        initial = prepare_render(self.project, config_path=DEFAULT, approve=True)
        self.assertEqual(initial['manifest_status'], 'COMPILED')
        path = self.project.folder / 'editing_plan.json'
        old = json.loads(path.read_text(encoding='utf-8'))
        old['plan_meta']['cache_key'] = 'stale-cache-key'
        atomic_write_json(path, old)
        before = path.read_bytes()
        atomic_write_json(self.project.folder / 'agent_scene_plan.json', {'invalid': True})
        with patch('ai_video_editor.agent.scene_planner.build_scene_editing_plan',
                   side_effect=ValidationError('Cannot build new plan')):
            with self.assertRaises(ValidationError):
                prepare_render(self.project, config_path=DEFAULT, approve=True,
                               replace_stale_plan=True)
        self.assertEqual(path.read_bytes(), before)

    def test_provider_without_key_rejected_before_generation(self):
        add_provider(self.projects, provider_id='vendorkey', label='Vendor',
                     base_url='https://api.vendor.example/v1')
        add_model(self.projects, provider_id='vendorkey', task='script', model='sample-model')
        from ai_video_editor.api.provider_settings import save_choices
        save_choices(self.projects, {'script': {'provider': 'vendorkey', 'model': 'sample-model'}})
        with patch('ai_video_editor.api.app.get_key', return_value=None), \
             patch('ai_video_editor.api.app.openai_generate') as send:
            response = self.client.post(self.api + '/script/generate', json={
                'requirement': 'Ocean story', 'replace': True})
            self.assertEqual(response.status_code, 422, response.text)
            send.assert_not_called()


class CliErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = ProjectManager(self.root / 'projects', DEFAULT).create_project('demo', 'Demo')
        self.args = ['--projects-dir', str(self.root/'projects'), '--default-config', str(DEFAULT)]

    def test_negative_limits_no_traceback(self):
        self.assertEqual(main(self.args + ['asset', 'limits', 'demo', '--image', '-1', '--audio', '1']), 1)

    def test_invalid_limits_usage_no_traceback(self):
        (self.project.folder / 'api_request_limits.json').write_text('broken', encoding='utf-8')
        self.assertEqual(main(self.args + ['asset', 'usage', 'demo']), 1)


if __name__ == '__main__':
    unittest.main()
