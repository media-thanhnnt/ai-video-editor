"""Phase 2.4.13: voice choice must reach single + batch TTS, persist and be validated."""
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
    HAS_UI = False

from ai_video_editor.agent.script_generator import create_script_draft
from ai_video_editor.project import ProjectManager

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SCRIPT = {
    'title':'Ocean', 'language':'vi', 'style':'documentary',
    'target_duration_seconds':20, 'summary':'Ocean', 'script':'Scene one and two',
    'scenes': [
        {'scene_id':'001','type':'image','narration':'Lời đọc một','prompt':'English prompt one','estimated_duration_seconds':10},
        {'scene_id':'002','type':'image','narration':'Lời đọc hai','prompt':'English prompt two','estimated_duration_seconds':10},
    ]
}

@unittest.skipUnless(HAS_UI, 'GUI deps missing')
class VoiceSelectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.manager=ProjectManager(Path(temp.name)/'projects', DEFAULT)
        project=self.manager.create_project('ocean','Ocean')
        create_script_draft(project,copy.deepcopy(SCRIPT),source='manual')
        self.client=TestClient(create_app(testing=True,projects_dir=self.manager.projects_dir,default_config=DEFAULT))
        self.root='/api/projects/ocean'
        key=patch('ai_video_editor.api.app.get_key',return_value='test-key')
        key.start();self.addCleanup(key.stop)

    def test_voice_persistence_and_validation(self):
        r=self.client.get(self.root+'/assets/voice-settings')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['provider'],'openai')
        self.assertIn('coral',r.json()['voices'])
        r=self.client.put(self.root+'/assets/voice-settings',json={'voice':'coral'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get(self.root+'/assets/voice-settings').json()['selected_voice'],'coral')
        self.assertEqual(self.client.put(self.root+'/assets/voice-settings',json={'voice':'Kore'}).status_code,422)
        self.assertEqual(self.client.get(self.root+'/assets/voice-settings').json()['selected_voice'],'coral')
        self.assertTrue((self.manager.projects_dir/'ocean'/'voice_preferences.json').exists())

    def test_single_voice_respects_saved_and_explicit_choice(self):
        self.client.put(self.root+'/assets/voice-settings',json={'voice':'coral'})
        with patch('ai_video_editor.api.app.generate_openai_asset',return_value=(b'ID3sample','.mp3','gpt-4o-mini-tts')) as gen:
            r=self.client.post(self.root+'/assets/generate',json={'scene_id':'001','kind':'audio','confirmed':True})
            self.assertEqual(r.status_code,201,r.text)
            self.assertEqual(gen.call_args.kwargs['voice'],'coral')
            self.assertEqual(gen.call_args.args[1],'Lời đọc một')
            r=self.client.post(self.root+'/assets/generate',json={'scene_id':'002','kind':'audio','voice':'nova','confirmed':True})
            self.assertEqual(r.status_code,201,r.text)
            self.assertEqual(gen.call_args.kwargs['voice'],'nova')
            self.assertEqual(self.client.get(self.root+'/assets/voice-settings').json()['selected_voice'],'coral')

    def test_invalid_voice_does_not_consume_quota(self):
        before=self.client.get(self.root+'/assets/status').json()['api_usage']['used']['audio']
        r=self.client.post(self.root+'/assets/generate-job',json={'scene_id':'001','kind':'audio','voice':'Kore','confirmed':True})
        self.assertEqual(r.status_code,422,r.text)
        after=self.client.get(self.root+'/assets/status').json()['api_usage']['used']['audio']
        self.assertEqual(after,before)

    def test_batch_voice_sent_to_each_scene(self):
        self.client.put(self.root+'/assets/voice-settings',json={'voice':'shimmer'})
        with patch('ai_video_editor.api.app.generate_openai_asset',return_value=(b'ID3audio','.mp3','gpt-4o-mini-tts')) as gen:
            r=self.client.post(self.root+'/assets/batch-job',json={'scene_ids':['001','002'],'kind':'audio','confirmed':True})
            self.assertEqual(r.status_code,202,r.text)
            job=r.json()['job_id']
            for _ in range(100):
                result=self.client.get(self.root+'/jobs/'+job).json()
                if result['status'] in ('succeeded','failed'):
                    break
                time.sleep(.02)
            self.assertEqual(result['status'],'succeeded',result)
            self.assertEqual(gen.call_count,2)
            self.assertEqual([c.kwargs['voice'] for c in gen.call_args_list],['shimmer','shimmer'])
            self.assertEqual([c.args[1] for c in gen.call_args_list],['Lời đọc một','Lời đọc hai'])

    def test_gemini_voice_options_and_single_generation(self):
        from ai_video_editor.api.provider_settings import save_choices, load_choices
        current = load_choices(self.manager.projects_dir)
        current['audio'] = {'provider':'gemini','model':'gemini-2.5-flash-preview-tts'}
        save_choices(self.manager.projects_dir,current)
        r=self.client.get(self.root+'/assets/voice-settings')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['selected_voice'],'Kore')
        self.assertIn('Aoede',r.json()['voices'])
        self.assertNotIn('alloy',r.json()['voices'])
        self.assertEqual(self.client.put(self.root+'/assets/voice-settings',json={'voice':'Aoede'}).status_code,200)
        with patch('ai_video_editor.api.app.gemini_asset',return_value=(b'RIFFdata','.wav','gemini-2.5-flash-preview-tts')) as generate:
            result=self.client.post(self.root+'/assets/generate',json={'scene_id':'001','kind':'audio','confirmed':True})
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(generate.call_args.kwargs['voice'],'Aoede')
            self.assertEqual(generate.call_args.args[1],'Lời đọc một')

    def test_ui_has_batch_voice_selector(self):
        r=self.client.get('/asset-workspace')
        self.assertEqual(r.status_code,200)
        self.assertIn('id="voiceChoice"',r.text)
        self.assertIn("'audio',el('voiceChoice').value",r.text)
