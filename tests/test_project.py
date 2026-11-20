import copy
import json
import tempfile
import unittest
from pathlib import Path

from ai_video_editor.project import (ProjectManager, ValidationError, validate_config,
                                     validate_storyboard, ensure_inside)

ROOT = Path(__file__).resolve().parents[1]

class ProjectManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = ProjectManager(Path(self.temp.name) / 'projects', ROOT / 'config/default_project.json')
        self.project = self.manager.create_project('demo_001', 'Bí ẩn đại dương')

    def test_create_structure_and_defaults(self):
        self.assertEqual(self.project.state['status'], 'CREATED')
        self.assertEqual(self.project.config['output']['fps'], '30/1')
        for name in ('assets/images','assets/audio','assets/music','assets/fonts','work/scenes','output','reports'):
            self.assertTrue((self.project.folder / name).is_dir())
        self.assertEqual(self.manager.list_projects(), ['demo_001'])

    def test_unicode_roundtrip(self):
        s = copy.deepcopy(self.project.storyboard)
        s['scenes'] = [{'scene_id':'001','order':1,'narration':'Ánh sáng đại dương 日本語',
                        'image':'assets/images/scene_001.png','audio':'assets/audio/scene_001.mp3'}]
        self.manager.save_project('demo_001', storyboard=s)
        self.assertEqual(self.manager.load_project('demo_001').storyboard['scenes'][0]['narration'],s['scenes'][0]['narration'])

    def test_duplicate_project_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.manager.create_project('demo_001', 'Another')

    def test_project_id_traversal_rejected(self):
        with self.assertRaises(ValidationError):
            self.manager.create_project('../escape', 'Bad')

    def test_duplicate_scene_rejected(self):
        s = copy.deepcopy(self.project.storyboard)
        scene = {'scene_id':'001','order':1,'narration':'Test','image':'assets/images/a.png','audio':'assets/audio/a.wav'}
        s['scenes'] = [scene, dict(scene,order=2)]
        with self.assertRaisesRegex(ValidationError, 'duplicate'):
            validate_storyboard(s)

    def test_scene_order_mismatch_rejected(self):
        s = copy.deepcopy(self.project.storyboard)
        s['scenes']=[{'scene_id':'002','order':2,'narration':'Test','image':'assets/images/b.png','audio':'assets/audio/b.wav'}]
        with self.assertRaisesRegex(ValidationError, 'order'):
            validate_storyboard(s)

    def test_outside_path_rejected(self):
        s = copy.deepcopy(self.project.storyboard)
        s['scenes']=[{'scene_id':'001','order':1,'narration':'Test','image':'../outside.png','audio':'assets/audio/a.wav'}]
        with self.assertRaises(ValidationError):
            self.manager.save_project('demo_001', storyboard=s)

    def test_unsupported_schema_rejected(self):
        cfg = copy.deepcopy(self.project.config)
        cfg['schema_version']='2.0'
        with self.assertRaisesRegex(ValidationError, 'schema_version'):
            validate_config(cfg)

    def test_invalid_fps_rejected(self):
        cfg = copy.deepcopy(self.project.config)
        cfg['output']['fps']='30/0'
        with self.assertRaises(ValidationError):
            validate_config(cfg)

    def test_invalid_resolution_rejected(self):
        cfg = copy.deepcopy(self.project.config)
        cfg['output']['width']=1919
        with self.assertRaises(ValidationError):
            validate_config(cfg)

    def test_override_isolation(self):
        p = self.manager.create_project('alt_002', 'Video mới',config_overrides={'output':{'width':1280,'height':720}})
        self.assertEqual(p.config['output']['width'],1280)
        self.assertEqual(self.manager.load_project('demo_001').config['output']['width'],1920)

    def test_unknown_override_rejected(self):
        with self.assertRaises(ValidationError):
            self.manager.create_project('alt_002','Test',config_overrides={'unknown_key':3})
        self.assertFalse((self.manager.projects_dir/'alt_002').exists())

    def test_path_is_within_project(self):
        path=ensure_inside(self.project.folder,'assets/images/test.png')
        self.assertTrue(path.is_relative_to(self.project.folder))

    def test_bad_json_returns_clear_error(self):
        (self.project.folder/'storyboard.json').write_text('{broken',encoding='utf-8')
        with self.assertRaisesRegex(ValidationError,'Cannot read JSON'):
            self.manager.load_project('demo_001')

    def test_single_track_deferred(self):
        s=copy.deepcopy(self.project.storyboard)
        s['voice_mode']='single_track'
        with self.assertRaises(ValidationError):
            validate_storyboard(s)

if __name__ == '__main__':
    unittest.main()
