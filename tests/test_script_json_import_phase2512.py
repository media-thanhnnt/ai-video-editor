"""Portable editable Script JSON: no API calls, safe replacement and revision history."""
import copy
import tempfile
import unittest
from pathlib import Path
try:
    from fastapi.testclient import TestClient
    from ai_video_editor.api.app import create_app
    HAS_UI=True
except (ImportError, RuntimeError):
    HAS_UI=False

DEFAULT = Path(__file__).resolve().parents[1] / 'config/default_project.json'
SAMPLE = {
    'title':'Ocean', 'language':'Tiếng Việt', 'style':'documentary',
    'target_duration_seconds':10, 'summary':'Đại dương', 'script':'Sóng biển.',
    'scenes':[{'scene_id':'001','type':'image','narration':'Sóng biển.',
               'prompt':'A quiet ocean', 'estimated_duration_seconds':10}],
}

@unittest.skipUnless(HAS_UI, 'GUI dependencies are not installed')
class EditableScriptImportTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'projects'
        self.client=TestClient(create_app(testing=True, projects_dir=self.root, default_config=DEFAULT))
        self.assertEqual(self.client.post('/api/projects',json={'project_id':'ocean','title':'Ocean'}).status_code,201)
        self.url='/api/projects/ocean/script'

    def imp(self, content, replace=False):
        return self.client.post(self.url+'/import',json={'content':content,'replace':replace})

    def test_create_export_modify_import_and_reapprove(self):
        r=self.imp({'format':'ai-video-editor-script','format_version':1,'content':SAMPLE})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['draft']['status'],'AWAITING_SCRIPT_APPROVAL')
        self.assertEqual(self.client.post(self.url+'/approve').status_code,200)
        portable=self.client.get(self.url+'/editable-export').json()
        self.assertNotIn('approved_hash',portable)
        self.assertNotIn('usage',portable)
        portable['content']['scenes'][0]['narration']='Sóng biển rất đẹp.'
        blocked=self.imp(portable)
        self.assertEqual(blocked.status_code,409)
        self.assertEqual(self.client.get(self.url).json()['status'],'SCRIPT_APPROVED')
        r=self.imp(portable,replace=True)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['changed'])
        self.assertEqual(r.json()['draft']['status'],'AWAITING_SCRIPT_APPROVAL')
        self.assertIsNone(r.json()['draft']['approved_hash'])
        self.assertEqual(self.client.post(self.url+'/approve').status_code,200)

    def test_archive_derived_files_on_edit(self):
        self.imp(SAMPLE)
        folder=self.root/'ocean'
        (folder/'agent_scene_plan.json').write_text('{"status":"SCENE_PLAN_APPROVED"}',encoding='utf-8')
        edited=copy.deepcopy(SAMPLE);edited['title']='Updated Ocean'
        r=self.imp(edited,replace=True)
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse((folder/'agent_scene_plan.json').exists())
        self.assertTrue(list((folder/'history').rglob('agent_scene_plan.json')))
        self.assertTrue((folder/'script_revision_pending.json').exists())

    def test_invalid_json_structure_preserves_script(self):
        self.imp(SAMPLE)
        invalid=copy.deepcopy(SAMPLE);invalid['scenes'][0]['type']='bad'
        r=self.imp(invalid,replace=True)
        self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(self.client.get(self.url).json()['content'],SAMPLE)
        self.assertEqual(self.imp({'format':'ai-video-editor-script','format_version':999,'content':SAMPLE},replace=True).status_code,422)

    def test_identical_import_does_not_reset_approval(self):
        self.imp(SAMPLE);self.client.post(self.url+'/approve')
        r=self.imp(self.client.get(self.url+'/editable-export').json(),replace=True)
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['changed'])
        self.assertEqual(r.json()['draft']['status'],'SCRIPT_APPROVED')

    def test_existing_export_brief_can_be_imported(self):
        self.imp(SAMPLE)
        self.client.post(self.url+'/approve')
        self.assertEqual(self.client.post(self.url+'/export',json={'replace':False}).status_code,200)
        import json
        brief=json.loads((self.root/'ocean'/'agent_brief_generated.json').read_text(encoding='utf-8'))
        brief['scenes'][0]['narration']='Lời thoại mới từ brief.'
        r=self.imp(brief,replace=True)
        self.assertEqual(r.status_code,200,r.text)
        new=r.json()['draft']['content']
        self.assertEqual(new['scenes'][0]['narration'],'Lời thoại mới từ brief.')
        self.assertEqual(new['scenes'][0]['estimated_duration_seconds'],10)
        self.assertEqual(new['script'],'Lời thoại mới từ brief.')
        self.assertEqual(new['language'],'Tiếng Việt')

    def test_brief_different_scenes_rejected(self):
        self.imp(SAMPLE)
        brief={'title':'Ocean','scenes':[{'scene_id':'999','type':'image','narration':'Other','prompt':'Prompt'}]}
        self.assertEqual(self.imp(brief,replace=True).status_code,422)
        self.assertEqual(self.client.get(self.url).json()['content'],SAMPLE)

    def test_gui_controls_are_present(self):
        page=self.client.get('/script-editor').text
        for text in ('export-json','import-json','import-file','editable.json'):
            self.assertIn(text,page)

if __name__=='__main__': unittest.main()
