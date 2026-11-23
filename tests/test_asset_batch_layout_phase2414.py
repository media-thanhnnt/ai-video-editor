"""Regression checks for latest Asset Workspace layout (batch controls between 3 and 4)."""
import unittest
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / 'src/ai_video_editor/api/static/asset-workspace.html'


class BatchActionPlacementTests(unittest.TestCase):
    def test_batch_controls_between_limits_and_scene_list(self):
        content = HTML.read_text(encoding='utf-8')
        limit_start = content.index('data-section="3"')
        batch_start = content.index('aria-label="Thao tác Batch API"')
        scenes_start = content.index('data-section="4"')
        self.assertLess(limit_start, batch_start)
        self.assertLess(batch_start, scenes_start)
        for element in ('batchImage', 'batchAudio'):
            self.assertLess(content.index(f'id="{element}"'), scenes_start)
            self.assertLess(content.index(f'id="{element}"'), content.index('id="scene-list"'))

    def test_voice_settings_available_before_batch_actions(self):
        content = HTML.read_text(encoding='utf-8')
        self.assertLess(content.index('id="voiceChoice"'), content.index('id="batchAudio"'))
        self.assertIn("el('batchImage').onclick", content)
        self.assertIn("el('batchAudio').onclick", content)
        self.assertIn("el('selectall').onchange", content)


if __name__ == '__main__':
    unittest.main()
