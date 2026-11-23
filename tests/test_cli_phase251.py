"""Phase 2.5.1 command parity tests — all paid API operations mocked."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

from ai_video_editor.cli import main
from ai_video_editor.cli_assets import generate, planned_scenes
from ai_video_editor.project import ProjectManager, ValidationError
from ai_video_editor.api.provider_settings import load_choices, add_model, save_choices
from ai_video_editor.api.limits import usage, set_limits

ROOT = Path(__file__).resolve().parents[1]

class CliFeatureParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / 'projects'
        self.manager = ProjectManager(self.projects, ROOT / 'config/default_project.json')
        self.project = self.manager.create_project('demo', 'Demo')
        script = {'schema_version':'2.2', 'project_id':'demo','status':'AWAITING_SCRIPT_APPROVAL',
                  'source':'manual','requirement':'','approved_hash':None,
                  'content':{'title':'Demo','language':'vi','style':'documentary',
                   'target_duration_seconds':6,'summary':'demo','script':'Xin chào.',
                   'scenes':[{'scene_id':'001','type':'image','narration':'Xin chào.',
                              'prompt':'Blue ocean image prompt in English',
                              'estimated_duration_seconds':6}]},
                  'usage':{'provider':'manual','input_tokens':0,'output_tokens':0,
                           'total_tokens':0,'estimated_cost_usd':0}}
        from ai_video_editor.project import atomic_write_json
        atomic_write_json(self.project.folder/'agent_script_draft.json',script)

    def invoke(self,*argv):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            rc=main(['--projects-dir',str(self.projects),'--default-config',str(ROOT/'config/default_project.json'),*argv])
        return rc,out.getvalue(),err.getvalue()

    def test_provider_list_and_set_persist(self):
        rc,out,err=self.invoke('provider','list')
        self.assertEqual(rc,0,err)
        self.assertIn('gemini',json.loads(out)['providers'])
        rc,out,err=self.invoke('provider','set','audio','gemini','--model','gemini-2.5-flash-preview-tts')
        self.assertEqual(rc,0,err)
        self.assertEqual(load_choices(self.projects)['audio']['provider'],'gemini')

    def test_provider_catalog_add_model(self):
        rc,out,err=self.invoke('provider','add','myapi','--label','My API','--base-url','https://api.example.org/v1')
        self.assertEqual(rc,0,err)
        rc,out,err=self.invoke('provider','add-model','myapi','--task','audio','--model','my-tts')
        self.assertEqual(rc,0,err)
        rc,out,err=self.invoke('provider','set','audio','myapi','--model','my-tts')
        self.assertEqual(rc,0,err)
        self.assertEqual(load_choices(self.projects)['audio'],{'provider':'myapi','model':'my-tts'})

    def test_no_key_no_quota(self):
        # Never read the developer's real OS keyring / env keys: a stored key made this
        # test send a real, billable TTS request.
        with patch('ai_video_editor.cli_assets.get_key', return_value=None),\
             patch('ai_video_editor.cli_assets.generate_openai_asset') as openai_call,\
             patch('ai_video_editor.cli_assets.gemini_asset') as gemini_call:
            rc,out,err=self.invoke('asset','generate-voice','demo','--scene','001','--yes')
        openai_call.assert_not_called()
        gemini_call.assert_not_called()
        self.assertEqual(rc,1)
        self.assertIn('API key',err)
        self.assertEqual(usage(self.project)['used']['audio'],0)

    def test_voice_uses_narration_not_media_prompt(self):
        call=[]
        def fake(kind,prompt,**kwargs):
            call.append((kind,prompt,kwargs['voice']))
            return b'ID3abc','.mp3','gpt-4o-mini-tts'
        with patch('ai_video_editor.cli_assets.get_key',return_value='test-not-real-key'),\
             patch('ai_video_editor.cli_assets.generate_openai_asset',side_effect=fake):
            result=generate(self.project,self.projects,kind='audio',scene_ids=['001'],
                            voice='nova',yes=True,output=lambda x:None)
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(call,[('audio','Xin chào.','nova')])
        self.assertEqual(usage(self.project)['used']['audio'],1)
        self.assertTrue((self.project.folder/result['results'][0]['file']).exists())

    def test_batch_uses_same_voice_and_selection(self):
        with patch('ai_video_editor.cli_assets.get_key',return_value='key-12345678'),\
             patch('ai_video_editor.cli_assets.generate_openai_asset',return_value=(b'ID3a','.mp3','tts-1')) as fake:
            result=generate(self.project,self.projects,kind='audio',scene_ids=None,
                            voice='Kore' if False else 'alloy',yes=True,output=lambda x:None)
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(fake.call_args.kwargs['voice'],'alloy')

    def test_batch_quota_fails_before_paid_operation(self):
        set_limits(self.project,image=0,audio=1)
        with patch('ai_video_editor.cli_assets.get_key',return_value='key-12345678'),\
             patch('ai_video_editor.cli_assets.generate_openai_asset') as fake:
            with self.assertRaisesRegex(ValidationError,'Only 0 image'):
                generate(self.project,self.projects,kind='image',scene_ids=['001'],yes=True)
            fake.assert_not_called()
        self.assertEqual(usage(self.project)['used']['image'],0)

    def test_noninteractive_requires_yes_without_spending(self):
        with patch('ai_video_editor.cli_assets.get_key',return_value='key-12345678'),\
             patch('ai_video_editor.cli_assets.generate_openai_asset') as fake,\
             patch('sys.stdin',io.StringIO('')):
            with self.assertRaisesRegex(ValidationError,'--yes'):
                generate(self.project,self.projects,kind='audio',scene_ids=['001'],output=lambda x:None)
            fake.assert_not_called()
        self.assertEqual(usage(self.project)['used']['audio'],0)

    def test_script_uses_configured_provider_when_requested(self):
        from ai_video_editor.cli_assets import generate_text_with_settings
        with patch('ai_video_editor.cli_assets.get_key', return_value='dummy-key'),\
             patch('ai_video_editor.agent.script_generator.openai_generate',
                   return_value=({'title':'x'}, {'provider':'openai','total_tokens':1})) as mock:
            content, usage_data, source = generate_text_with_settings(self.projects,'script','example')
            self.assertEqual(source,'openai')
            self.assertEqual(content, {'title':'x'})
            mock.assert_called_once()

    def test_cli_legacy_help_still_available(self):
        for action in ('compose','agent-bind','agent-script','agent-scene-plan'):
            out=io.StringIO()
            with redirect_stdout(out),self.assertRaises(SystemExit) as got:
                main([action,'--help'])
            self.assertEqual(got.exception.code,0)

    def test_image_batch_and_limits_cli(self):
        rc,out,err=self.invoke('asset','limits','demo','--image','1000','--audio','300')
        self.assertEqual(rc,0,err)
        self.assertEqual(json.loads(out)['limits']['image'],1000)
        self.assertEqual(planned_scenes(self.project,'image',None)[0][0],'001')
        self.assertEqual(planned_scenes(self.project,'audio',None)[0][0],'001')

if __name__=='__main__':
    unittest.main()
