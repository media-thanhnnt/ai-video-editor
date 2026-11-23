"""Phase 2.5.1.6 review fixes. Every provider call is mocked: no network, no keyring."""
import base64
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_video_editor.agent.asset_workflow import (generate_openai_asset, openai_image_size,
                                                  output_aspect_ratio)
from ai_video_editor.api import gemini_provider
from ai_video_editor.project import ProjectManager, ValidationError, atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32


def _project(width, height):
    return SimpleNamespace(config={'output': {'width': width, 'height': height}})


class AspectRatioTests(unittest.TestCase):
    def test_output_aspect_ratio_follows_project_frame(self):
        self.assertEqual(output_aspect_ratio(_project(1920, 1080)), '16:9')
        self.assertEqual(output_aspect_ratio(_project(1080, 1920)), '9:16')
        self.assertEqual(output_aspect_ratio(_project(1080, 1080)), '1:1')
        self.assertEqual(output_aspect_ratio(SimpleNamespace(config={})), '16:9')

    def test_openai_size_matches_orientation(self):
        self.assertEqual(openai_image_size('16:9'), '1536x1024')
        self.assertEqual(openai_image_size('9:16'), '1024x1536')
        self.assertEqual(openai_image_size('1:1'), '1024x1024')

    def test_gemini_image_requests_aspect_ratio(self):
        payload = {'candidates': [{'content': {'parts': [
            {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(PNG).decode()}}]}}]}
        with patch.object(gemini_provider, 'request_model', return_value=payload) as call:
            data, ext, _ = gemini_provider.generate_asset('image', 'A cat', 'gemini-2.5-flash-image',
                                                          aspect_ratio='16:9')
        self.assertEqual((data, ext), (PNG, '.png'))
        generation = call.call_args.args[2]
        self.assertEqual(generation['imageConfig'], {'aspectRatio': '16:9'})

    def test_openai_image_uses_requested_size(self):
        body = json.dumps({'data': [{'b64_json': base64.b64encode(PNG).decode()}]}).encode()
        with patch('ai_video_editor.agent.asset_workflow._post_json', return_value=body) as post:
            generate_openai_asset('image', 'A cat', model='gpt-image-2', api_key='k' * 10, size='1024x1536')
        self.assertEqual(post.call_args.args[1]['size'], '1024x1536')


class VoiceTests(unittest.TestCase):
    def test_gemini_pcm_uses_sample_rate_from_mime(self):
        pcm = b'\x00\x00' * 480
        payload = {'candidates': [{'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=48000', 'data': base64.b64encode(pcm).decode()}}]}}]}
        with patch.object(gemini_provider, 'request_model', return_value=payload):
            data, ext, _ = gemini_provider.generate_asset('audio', 'Xin chào', 'gemini-2.5-flash-preview-tts')
        self.assertEqual(ext, '.wav')
        with wave.open(io.BytesIO(data)) as wav:
            self.assertEqual(wav.getframerate(), 48000)

    def test_tts_instructions_only_for_models_that_support_them(self):
        sent = []
        def fake(path, body, **kwargs):
            sent.append(body)
            return b'ID3' + b'0' * 16
        with patch('ai_video_editor.agent.asset_workflow._post_json', side_effect=fake):
            generate_openai_asset('audio', 'Xin chào', model='gpt-4o-mini-tts', api_key='k' * 10,
                                  instructions='Read calmly.')
            generate_openai_asset('audio', 'Xin chào', model='tts-1', api_key='k' * 10,
                                  instructions='Read calmly.')
            generate_openai_asset('audio', 'Xin chào', model='my-tts', api_key='k' * 10, compatible=True,
                                  base_url='https://api.example.org/v1', instructions='Read calmly.')
        self.assertEqual(sent[0]['instructions'], 'Read calmly.')
        self.assertNotIn('instructions', sent[1])
        self.assertNotIn('instructions', sent[2])


class CliRobustnessTests(unittest.TestCase):
    def test_cli_output_survives_non_utf8_stream(self):
        from ai_video_editor.cli import _utf8_stdio
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp932')
        with patch.object(sys, 'stdout', stream), patch.object(sys, 'stderr', stream):
            _utf8_stdio()
            print('Đặc tả 仕様')
            stream.flush()
        self.assertIn('Đặc tả 仕様', raw.getvalue().decode('utf-8'))

    def test_cap_reached_mid_batch_is_a_clean_validation_error(self):
        from ai_video_editor.cli_assets import generate
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / 'projects'
            project = ProjectManager(projects, ROOT / 'config/default_project.json').create_project('demo', 'Demo')
            atomic_write_json(project.folder / 'agent_script_draft.json', {
                'schema_version': '2.2', 'project_id': 'demo', 'status': 'AWAITING_SCRIPT_APPROVAL',
                'source': 'manual', 'requirement': '', 'approved_hash': None,
                'content': {'title': 'Demo', 'language': 'vi', 'style': 'documentary',
                            'target_duration_seconds': 6, 'summary': 'demo', 'script': 'Xin chào.',
                            'scenes': [{'scene_id': '001', 'type': 'image', 'narration': 'Xin chào.',
                                        'prompt': 'Ocean', 'estimated_duration_seconds': 6}]},
                'usage': {'provider': 'manual', 'input_tokens': 0, 'output_tokens': 0,
                          'total_tokens': 0, 'estimated_cost_usd': 0}})
            with patch('ai_video_editor.cli_assets.get_key', return_value='key-12345678'),\
                 patch('ai_video_editor.cli_assets.reserve', side_effect=ValueError('Local audio daily cap reached')),\
                 patch('ai_video_editor.cli_assets.generate_openai_asset') as openai_call,\
                 patch('ai_video_editor.cli_assets.gemini_asset') as gemini_call:
                with self.assertRaises(ValidationError):
                    generate(project, projects, kind='audio', scene_ids=['001'], yes=True, output=lambda _: None)
            openai_call.assert_not_called()
            gemini_call.assert_not_called()

    def test_gui_render_subprocess_forces_utf8(self):
        from ai_video_editor.api import workflow
        captured = {}
        def fake_popen(argv, **kwargs):
            captured.update(kwargs)
            raise RuntimeError('stop before running FFmpeg')
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / 'projects'
            ProjectManager(projects, ROOT / 'config/default_project.json').create_project('demo', 'Demo')
            with patch.object(workflow.subprocess, 'Popen', side_effect=fake_popen):
                with self.assertRaises(RuntimeError):
                    workflow.render_cli('demo', projects, ROOT / 'config/default_project.json', lambda *a: None)
        self.assertEqual(captured['env']['PYTHONIOENCODING'], 'utf-8')


if __name__ == '__main__':
    unittest.main()
