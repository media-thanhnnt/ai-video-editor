import json
import tempfile
import unittest
from pathlib import Path

from ai_video_editor.agent import (ToolRegistry, registry, create_manifest, bind_asset,
                                   approve_manifest, compile_storyboard, missing_assets, load_manifest)
from ai_video_editor.project import ProjectManager, ValidationError, read_json

ROOT=Path(__file__).resolve().parents[1]

class Phase21Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pm=ProjectManager(Path(self.tmp.name)/'projects',ROOT/'config/default_project.json')
        self.project=self.pm.create_project('agent_demo','Agent Demo')
        self.brief={'title':'Test clip','scenes':[
            {'scene_id':'001','type':'image','prompt':'Blue ocean','narration':'Xin chào'},
            {'scene_id':'002','type':'video_clip','prompt':'A wave','narration':''}]}

    def _write(self,relative):
        path=self.project.folder/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'placeholder for agent manifest binding; media validated by Core')

    def _complete(self):
        create_manifest(self.project,self.brief)
        self._write('assets/images/one.png')
        self._write('assets/audio/one.wav')
        self._write('assets/videos/two.mp4')
        bind_asset(self.project,'001','media','assets/images/one.png')
        bind_asset(self.project,'001','voice','assets/audio/one.wav')
        bind_asset(self.project,'002','media','assets/videos/two.mp4')

    def test_complete_compile(self):
        self._complete()
        self.assertEqual(missing_assets(self.project,load_manifest(self.project)),[])
        approve_manifest(self.project)
        result=compile_storyboard(self.project)
        self.assertEqual([s['type'] for s in result['scenes']],['image','video_clip'])
        self.assertEqual(read_json(self.project.folder/'storyboard.json')['scenes'][0]['audio'],'assets/audio/one.wav')

    def test_unapproved_not_compilable(self):
        self._complete()
        with self.assertRaises(ValidationError):
            compile_storyboard(self.project)

    def test_incomplete_not_approvable(self):
        create_manifest(self.project,self.brief)
        with self.assertRaises(ValidationError):
            approve_manifest(self.project)

    def test_reject_paths_outside_project(self):
        create_manifest(self.project,self.brief)
        with self.assertRaises(ValidationError):
            bind_asset(self.project,'001','media','../secret.png')
        with self.assertRaises(ValidationError):
            bind_asset(self.project,'001','media','assets/audio/foo.wav')

    def test_does_not_overwrite_existing_storyboard_without_flag(self):
        self._complete()
        approve_manifest(self.project)
        compile_storyboard(self.project)
        with self.assertRaises(ValidationError):
            compile_storyboard(self.project)
        self.assertEqual(len(compile_storyboard(self.project,replace_storyboard=True)['scenes']),2)

    def test_registry_allowlist(self):
        names={v['name'] for v in registry().describe()}
        self.assertEqual(names,{'create_manifest','bind_asset','approve_manifest','compile_storyboard'})
        with self.assertRaises(ValidationError):
            registry().call('run_shell',command='rm -rf /')

    def test_changed_plan_requires_new_approval(self):
        self._complete()
        approve_manifest(self.project)
        data=load_manifest(self.project)
        data['scenes'][0]['narration']='Changed script'
        from ai_video_editor.project import atomic_write_json
        atomic_write_json(self.project.folder/'agent_manifest.json',data)
        with self.assertRaises(ValidationError):
            compile_storyboard(self.project)

if __name__=='__main__':
    unittest.main()
