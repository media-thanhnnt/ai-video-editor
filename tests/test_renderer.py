import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from ai_video_editor.renderer import RenderError, _prepare_source, motion_frame, verify_video


class MotionRenderTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((180, 320, 3), dtype=np.uint8)
        self.image[:, :, :] = (40, 70, 110)
        self.image[40:80, 120:180] = (250, 220, 160)

    def test_all_motions_keep_dimensions(self):
        for kind in ('static','zoom_in','zoom_out','pan_left','pan_right'):
            for index in (0, 14, 29):
                frame = motion_frame(self.image,kind,'medium',index,30,160,90)
                self.assertEqual(frame.shape,(90,160,3))

    def test_static_is_deterministic(self):
        self.assertTrue(np.array_equal(motion_frame(self.image,'static','subtle',0,30,160,90),
                                       motion_frame(self.image,'static','subtle',29,30,160,90)))

    def test_zoom_direction(self):
        # Distinct center feature grows under zoom-in and shrinks under zoom-out.
        self.assertFalse(np.array_equal(motion_frame(self.image,'zoom_in','strong',0,30,160,90),
                                        motion_frame(self.image,'zoom_in','strong',29,30,160,90)))

    def test_reject_invalid_motion(self):
        with self.assertRaises(RenderError):
            motion_frame(self.image,'rotate','medium',0,30,160,90)

    def test_reject_invalid_frame(self):
        with self.assertRaises(RenderError):
            motion_frame(self.image,'zoom_in','medium',31,30,160,90)

    def test_final_assembly_uses_supported_filtergraph_option(self):
        from inspect import getsource
        from ai_video_editor.renderer import _render_project_locked
        source = getsource(_render_project_locked)
        self.assertIn('complex_script_args(', source)
        from ai_video_editor.ffmpeg_support import complex_script_args
        self.assertEqual(complex_script_args(Path('a.ffgraph'), 9), ['-/filter_complex','a.ffgraph'])
        self.assertEqual(complex_script_args(Path('a.ffgraph'), 6), ['-filter_complex_script','a.ffgraph'])

    def test_prepare_image_modes(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'image.png'
            Image.new('RGB',(40,80),'red').save(p)
            for mode in ('cover','contain','blur_fill'):
                result=_prepare_source(p,160,90,2,mode)
                self.assertEqual(result.shape,(180,320,3))

    def test_invalid_fit(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'image.png'
            Image.new('RGB',(40,80),'red').save(p)
            with self.assertRaises(RenderError):
                _prepare_source(p,160,90,2,'invalid')


if __name__=='__main__':
    unittest.main()
