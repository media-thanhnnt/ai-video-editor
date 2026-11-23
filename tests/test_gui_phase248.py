"""Phase 2.4.8: consistent shared navigation and accessible collapsible Asset panels."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'src/ai_video_editor/api/static'
PAGES=['index.html','script-editor.html','scene-planner.html','asset-workspace.html','render-studio.html','api-settings.html']

class SharedSidebarTests(unittest.TestCase):
    def test_every_page_loads_same_versioned_sidebar(self):
        for filename in PAGES:
            with self.subTest(filename=filename):
                html=(ROOT/filename).read_text(encoding='utf-8')
                self.assertIn('id="app-sidebar"',html)
                self.assertIn('/static/sidebar.js?v=2511',html)
                self.assertIn('/static/sidebar.css?v=248',html)
                self.assertNotIn('Local Studio · Phase 2.4.1',html)
                # No legacy page-scoped sidebar layout rules.
                css=html.split('<style>',1)[1].split('</style>',1)[0]
                import re
                self.assertIsNone(re.search(r'(?<![\w.#-])aside(?:\s+[a-z][\w.]*)?\s*\{',css))

    def test_shadow_dom_isolates_sidebar_from_page_styles(self):
        js=(ROOT/'sidebar.js').read_text(encoding='utf-8')
        self.assertIn('attachShadow',js)
        self.assertIn('shadow.replaceChildren',js)
        for route in ('Dashboard','Script Editor','Scene Planner','Asset Workspace','Render Studio','API Settings'):
            self.assertIn(route,js)
        self.assertIn('aria-current',js)

    def test_asset_panels_are_numbered_and_collapsible(self):
        html=(ROOT/'asset-workspace.html').read_text(encoding='utf-8')
        labels=['1 · Chọn Project','2 · Tình trạng API &amp; Asset Binding',
                '3 · Giới hạn API','4 · Danh sách prompt theo scene']
        indices=[html.index(x) for x in labels]
        self.assertEqual(indices,sorted(indices))
        for i in range(1,5):
            self.assertIn(f'data-section="{i}"',html)
            self.assertIn(f'aria-controls="asset-section-{i}"',html)
            self.assertIn(f'id="asset-section-{i}"',html)
        self.assertEqual(html.count('class="section-toggle"'),4)
        self.assertIn('body.hidden=expanded',html)
        self.assertIn('aria-expanded',html)

if __name__=='__main__': unittest.main()
