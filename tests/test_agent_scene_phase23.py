import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_video_editor.project import ProjectManager, ValidationError, atomic_write_json, read_json
from ai_video_editor.agent.script_generator import create_script_draft, approve_script
from ai_video_editor.agent.scene_planner import (
    validate_proposal, create_scene_plan, load_scene_plan, approve_scene_plan,
    export_scene_brief, apply_scene_plan, generate_openai_scene_plan)
from ai_video_editor.planner import validate_editing_plan
from ai_video_editor.cli import main

SCRIPT={'title':'Ocean','language':'vi','style':'documentary','target_duration_seconds':12,
        'summary':'Sea','script':'Blue ocean then corals',
        'scenes':[{'scene_id':'001','type':'image','narration':'Blue ocean',
                   'prompt':'Ocean','estimated_duration_seconds':6},
                  {'scene_id':'002','type':'video_clip','narration':'',
                   'prompt':'Corals','estimated_duration_seconds':6}]}
PLAN={'style':'documentary','scenes':[
    {'scene_id':'001','media_type':'image','asset_prompt_en':'Wide aerial ocean 16:9',
     'estimated_duration_seconds':6,'motion':'pan_right','motion_intensity':'subtle',
     'transition_to_next':'dissolve','transition_length':'short','reason':'gentle pan'},
    {'scene_id':'002','media_type':'video_clip','asset_prompt_en':'Coral underwater 16:9',
     'estimated_duration_seconds':6,'motion':'static','motion_intensity':'subtle',
     'transition_to_next':'none','transition_length':None,'reason':'video already moving'}]}

class FakeResponse:
    def __init__(self, obj): self.obj=obj
    def __enter__(self):return self
    def __exit__(self,*a): return False
    def read(self):return json.dumps(self.obj).encode()

class TestScenePlanner(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.cfg=Path(__file__).resolve().parents[1]/'config/default_project.json'
        self.project=ProjectManager(self.root/'projects',self.cfg).create_project('ocean','Ocean')
        create_script_draft(self.project,SCRIPT,source='manual')
        approve_script(self.project)
    def tearDown(self):self.temp.cleanup()

    def test_approval_export_and_phase21_brief(self):
        created=create_scene_plan(self.project,PLAN)
        self.assertEqual(created['status'],'AWAITING_SCENE_PLAN_APPROVAL')
        with self.assertRaises(ValidationError):export_scene_brief(self.project)
        approve_scene_plan(self.project)
        result=export_scene_brief(self.project)
        self.assertEqual(result['scenes'],2)
        brief=read_json(self.project.folder/'agent_scene_brief.json')
        self.assertEqual(brief['scenes'][0]['prompt'],PLAN['scenes'][0]['asset_prompt_en'])
        self.assertEqual(brief['scenes'][0]['narration'],SCRIPT['scenes'][0]['narration'])

    def test_tamper_and_stale_script(self):
        create_scene_plan(self.project,PLAN);approve_scene_plan(self.project)
        doc=load_scene_plan(self.project)
        doc['content']['scenes'][0]['motion']='zoom_in'
        atomic_write_json(self.project.folder/'agent_scene_plan.json',doc)
        with self.assertRaisesRegex(ValidationError,'approval'):export_scene_brief(self.project)

    def test_incompatible_transition_rejected(self):
        bad=copy.deepcopy(PLAN)
        bad['scenes'][-1]['transition_to_next']='dissolve'
        bad['scenes'][-1]['transition_length']='short'
        with self.assertRaises(ValidationError):validate_proposal(bad,SCRIPT)

    def test_image_without_narration_rejected(self):
        bad=copy.deepcopy(PLAN)
        bad['scenes'][1]['media_type']='image'
        bad['scenes'][1]['motion']='static'
        with self.assertRaisesRegex(ValidationError,'narration'):
            validate_proposal(bad,SCRIPT)

    def test_no_openai_call_when_script_approval_invalid(self):
        create_scene_plan(self.project,PLAN)
        # Overwrite guard must fire before calling an external paid service.
        from unittest.mock import patch
        args=['--projects-dir',str(self.root/'projects'),'--default-config',str(self.cfg)]
        with patch('ai_video_editor.agent.scene_planner.generate_openai_scene_plan') as remote:
            self.assertEqual(main(args+['agent-scene-plan','ocean','--model','gpt-4.1-mini']),1)
            remote.assert_not_called()

    def test_video_motion_rejected(self):
        bad=copy.deepcopy(PLAN);bad['scenes'][1]['motion']='zoom_in'
        with self.assertRaises(ValidationError):validate_proposal(bad,SCRIPT)

    def test_script_change_invalidates_plan(self):
        create_scene_plan(self.project,PLAN);approve_scene_plan(self.project)
        script=read_json(self.project.folder/'agent_script_draft.json')
        script['content']['scenes'][0]['narration']='changed'
        atomic_write_json(self.project.folder/'agent_script_draft.json',script)
        with self.assertRaises(ValidationError):load_scene_plan(self.project)

    def test_apply_never_overwrites_and_requires_storyboard_match(self):
        create_scene_plan(self.project,PLAN);approve_scene_plan(self.project)
        with self.assertRaisesRegex(ValidationError,'Storyboard'):apply_scene_plan(self.project)
        sb=read_json(self.project.folder/'storyboard.json')
        sb['scenes']=[{'scene_id':'001','order':1,'type':'image','narration':'Blue ocean',
                      'image':'assets/images/a.png','audio':'assets/audio/a.wav'},
                     {'scene_id':'002','order':2,'type':'video_clip','narration':'',
                      'video':'assets/videos/b.mp4'}]
        atomic_write_json(self.project.folder/'storyboard.json',sb)
        # ProjectManager load is required because Project.storyboard is loaded in memory.
        reloaded=ProjectManager(self.root/'projects',self.cfg).load_project('ocean')
        applied=apply_scene_plan(reloaded)
        self.assertEqual(applied['source'],'ai_assisted')
        core=read_json(self.project.folder/'editing_plan.json')
        self.assertEqual(core['plan_meta']['source'],'ai_assisted')
        self.assertFalse(core['plan_meta']['approved_by_user'])
        validate_editing_plan(reloaded,core)
        with self.assertRaisesRegex(ValidationError,'exists'):apply_scene_plan(reloaded)

    def test_mock_openai(self):
        calls=[]
        def fake(req,timeout):
            calls.append(json.loads(req.data))
            return FakeResponse({'status':'completed','output':[{'type':'message','content':[
                {'type':'output_text','text':json.dumps(PLAN)}]}],
                'usage':{'input_tokens':100,'output_tokens':200,'total_tokens':300}})
        got,usage=generate_openai_scene_plan(SCRIPT,api_key='test-only',opener=fake)
        self.assertEqual(got,PLAN)
        self.assertEqual(usage['total_tokens'],300)
        self.assertFalse(calls[0]['store'])

    def test_cli_offline(self):
        source=self.root/'scene.json';source.write_text(json.dumps(PLAN),encoding='utf-8')
        args=['--projects-dir',str(self.root/'projects'),'--default-config',str(self.cfg)]
        self.assertEqual(main(args+['agent-scene-plan','ocean','--import-json',str(source)]),0)
        self.assertEqual(main(args+['agent-scene-status','ocean']),0)
        self.assertEqual(main(args+['agent-scene-approve','ocean']),0)
        self.assertEqual(main(args+['agent-scene-export','ocean']),0)

if __name__=='__main__':unittest.main()
