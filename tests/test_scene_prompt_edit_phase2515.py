"""Phase 2.5.1.5: explicit LLM prompt editing and safe re-edit after approval."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    UI = True
except (ImportError, RuntimeError):
    UI = False

from ai_video_editor.project import ProjectManager, atomic_write_json, read_json
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.agent.scene_planner import DEFAULT_SCENE_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/default_project.json'
SCRIPT = {'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':10,
          'summary':'Sea','script':'Blue water','scenes':[{'scene_id':'001','type':'image',
          'narration':'Blue water','prompt':'Blue sea','estimated_duration_seconds':10}]}
PLAN = {'style':'documentary','scenes':[{'scene_id':'001','media_type':'image',
        'asset_prompt_en':'Blue sea scene','estimated_duration_seconds':10,'motion':'static',
        'motion_intensity':'subtle','transition_to_next':'none','transition_length':None,
        'reason':'A calm opener'}]}

@unittest.skipUnless(UI, 'FastAPI/httpx not installed')
class ScenePromptTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name)/'projects'
        self.project=ProjectManager(self.folder,CONFIG).create_project('demo','Demo')
        create_script_draft(self.project,copy.deepcopy(SCRIPT),source='manual')
        approve_script(self.project)
        self.client=TestClient(create_app(testing=True,projects_dir=self.folder,default_config=CONFIG))
        self.base='/api/projects/demo/scene-plan'
        secret=patch('ai_video_editor.api.app._provider_key',return_value='mock-key')
        secret.start();self.addCleanup(secret.stop)

    def test_preview_contains_exact_default_and_approved_script(self):
        response=self.client.get(self.base+'/prompt-preview')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['instructions'],DEFAULT_SCENE_INSTRUCTIONS)
        self.assertIn('Blue water',response.json()['script_input'])
        self.assertNotIn('mock-key',response.text)
        page=self.client.get('/scene-planner').text
        self.assertIn('scene-instructions',page)
        self.assertIn('reset-prompt',page)
        self.assertIn('replace_downstream',page)

    def test_custom_prompt_reaches_openai_generator(self):
        instructions='Please write a gentle video plan with vivid image prompts.'
        with patch('ai_video_editor.api.app.generate_openai_scene_plan',return_value=(copy.deepcopy(PLAN),{'provider':'openai'})) as generator:
            response=self.client.post(self.base+'/generate',json={'instructions':instructions})
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(generator.call_args.kwargs['instructions'],instructions)

    def test_approve_plan_then_revise_with_downstream_archive(self):
        with patch('ai_video_editor.api.app.generate_openai_scene_plan',return_value=(copy.deepcopy(PLAN),{'provider':'openai'})):
            self.assertEqual(self.client.post(self.base+'/generate',json={}).status_code,201)
        self.assertEqual(self.client.post(self.base+'/approve').status_code,200)
        original=read_json(self.project.folder/'agent_scene_plan.json')
        self.client.post(self.base+'/export',json={})
        atomic_write_json(self.project.folder/'editing_plan.json',{'fake':'old plan for archive'})
        modified=copy.deepcopy(PLAN)
        modified['scenes'][0]['asset_prompt_en']='Different ocean view'
        refused=self.client.put(self.base,json={'content':modified})
        self.assertEqual(refused.status_code,409)
        self.assertEqual(read_json(self.project.folder/'agent_scene_plan.json'),original)
        success=self.client.put(self.base,json={'content':modified,'replace_downstream':True})
        self.assertEqual(success.status_code,200,success.text)
        self.assertEqual(success.json()['status'],'AWAITING_SCENE_PLAN_APPROVAL')
        self.assertFalse((self.project.folder/'editing_plan.json').exists())
        self.assertFalse((self.project.folder/'agent_scene_brief.json').exists())
        self.assertTrue((self.project.folder/'storyboard.json').exists())
        history=list((self.project.folder/'history').glob('scene_revision_*'))
        self.assertEqual(len(history),1)
        self.assertTrue((history[0]/'previous_agent_scene_plan.json').exists())
        self.assertTrue((history[0]/'editing_plan.json').exists())
        self.assertEqual(self.client.post(self.base+'/approve').status_code,200)
        self.assertEqual(self.client.get(self.base).json()['status'],'SCENE_PLAN_APPROVED')

    def test_no_change_preserves_approval_and_downstream(self):
        with patch('ai_video_editor.api.app.generate_openai_scene_plan',return_value=(copy.deepcopy(PLAN),{'provider':'openai'})):
            self.client.post(self.base+'/generate',json={})
        self.client.post(self.base+'/approve')
        self.client.post(self.base+'/export',json={})
        response=self.client.put(self.base,json={'content':PLAN})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['status'],'SCENE_PLAN_APPROVED')
        self.assertTrue((self.project.folder/'agent_scene_brief.json').exists())

    def test_short_prompt_rejected_before_paid_call(self):
        with patch('ai_video_editor.api.app.generate_openai_scene_plan') as generator:
            response=self.client.post(self.base+'/generate',json={'instructions':'short'})
        self.assertEqual(response.status_code,422)
        generator.assert_not_called()
