import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_video_editor.agent.script_generator import (
    validate_script, create_script_draft, load_script_draft,
    approve_script, export_agent_brief, openai_generate, digest)
from ai_video_editor.project import ProjectManager, ValidationError, read_json, atomic_write_json
from ai_video_editor.cli import main

SAMPLE = {'title': 'Đại dương bí ẩn', 'language': 'vi', 'style': 'documentary',
          'target_duration_seconds': 12, 'summary': 'Khám phá đại dương',
          'script': 'Dưới đại dương là cả một thế giới. Những rạn san hô rực rỡ.',
          'scenes': [
              {'scene_id':'001','type':'image','narration':'Dưới đại dương là cả một thế giới.',
               'prompt':'Cinematic ocean photo, 16:9','estimated_duration_seconds':6},
              {'scene_id':'002','type':'video_clip','narration':'',
               'prompt':'Cinematic moving camera across corals, 16:9',
               'estimated_duration_seconds':6}]}


class FakeResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.data).encode()


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = ProjectManager(root/'projects',Path(__file__).resolve().parents[1]/'config/default_project.json').create_project('script_demo','Script Demo')
    def tearDown(self): self.tmp.cleanup()

    def test_review_export_phase21_compatible(self):
        created=create_script_draft(self.project,SAMPLE,source='manual')
        self.assertEqual(created['status'],'AWAITING_SCRIPT_APPROVAL')
        with self.assertRaises(ValidationError): export_agent_brief(self.project)
        approve_script(self.project)
        exported=export_agent_brief(self.project)
        self.assertEqual(exported['scenes'],2)
        from ai_video_editor.agent.manifest import create_manifest
        manifest=create_manifest(self.project,read_json(self.project.folder/'agent_brief_generated.json'))
        self.assertEqual(len(manifest['scenes']),2)
        self.assertEqual(manifest['status'],'WAITING_FOR_ASSETS')

    def test_no_overwrite_without_flag(self):
        create_script_draft(self.project,SAMPLE,source='manual')
        with self.assertRaises(ValidationError):create_script_draft(self.project,SAMPLE,source='manual')
        approve_script(self.project)
        export_agent_brief(self.project)
        with self.assertRaises(ValidationError): export_agent_brief(self.project)

    def test_tamper_after_approval(self):
        create_script_draft(self.project,SAMPLE,source='manual')
        approve_script(self.project)
        draft=load_script_draft(self.project)
        draft['content']['script']='Changed content'
        atomic_write_json(self.project.folder/'agent_script_draft.json',draft)
        with self.assertRaises(ValidationError): export_agent_brief(self.project)

    def test_invalid_image_without_narration(self):
        bad=json.loads(json.dumps(SAMPLE))
        bad['scenes'][0]['narration']=''
        with self.assertRaises(ValidationError):validate_script(bad)

    def test_duplicate_scene_and_invalid_duration(self):
        bad=json.loads(json.dumps(SAMPLE))
        bad['scenes'][1]['scene_id']='001'
        with self.assertRaises(ValidationError):validate_script(bad)
        bad['scenes'][1]['scene_id']='002'
        bad['scenes'][0]['estimated_duration_seconds']=0
        with self.assertRaises(ValidationError):validate_script(bad)

    def test_missing_key(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValidationError,'OPENAI_API_KEY'):
                openai_generate('Create a 1-minute documentary')

    def test_mock_openai_provider_and_usage(self):
        received=[]
        def fake_open(req, timeout):
            received.append(json.loads(req.data.decode()))
            self.assertTrue(req.get_header('Authorization').startswith('Bearer '))
            return FakeResponse({'status':'completed','output':[
                {'type':'message','content':[{'type':'output_text','text':json.dumps(SAMPLE)}]}],
                'usage':{'input_tokens':100,'output_tokens':250,'total_tokens':350}})
        data,usage=openai_generate('Create a documentary',api_key='test-only',opener=fake_open)
        self.assertEqual(data['title'],SAMPLE['title'])
        self.assertEqual(usage['total_tokens'],350)
        self.assertEqual(received[0]['text']['format']['type'],'json_schema')
        self.assertFalse(received[0]['store'])

    def test_offline_cli_integration(self):
        path=Path(self.tmp.name)/'provided.json';path.write_text(json.dumps(SAMPLE),encoding='utf-8')
        args=['--projects-dir',str(self.project.folder.parent),'--default-config',
              str(Path(__file__).resolve().parents[1]/'config/default_project.json')]
        self.assertEqual(main(args+['agent-script','script_demo','--import-json',str(path)]),0)
        self.assertEqual(main(args+['agent-script-status','script_demo']),0)
        self.assertEqual(main(args+['agent-script-approve','script_demo']),0)
        self.assertEqual(main(args+['agent-script-export','script_demo']),0)
        self.assertTrue((self.project.folder/'agent_brief_generated.json').is_file())

if __name__ == '__main__': unittest.main()
