"""Phase 2.5.2 UI help/layout regressions (no paid calls)."""
import unittest
from pathlib import Path
from html.parser import HTMLParser

class _Tags(HTMLParser):
    def __init__(self): super().__init__(); self.tags=[]; self.text=[]
    def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))
    def handle_data(self,data): self.text.append(data)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'src' / 'ai_video_editor' / 'api' / 'static'

class HelpLayoutTests(unittest.TestCase):
    def test_help_content_and_cli_commands(self):
        html = (STATIC/'help.html').read_text(encoding='utf-8')
        page = _Tags(); page.feed(html)
        for marker in ('Quy trình trên Web UI','Quy trình bằng CLI','agent-script',
                       'agent-scene-plan','batch-voice','agent-bind','compose',
                       'Render Studio','API Settings'):
            self.assertIn(marker, ' '.join(page.text))
        self.assertTrue(any(t=='aside' and attrs.get('id')=='app-sidebar' for t,attrs in page.tags))
        self.assertIn("'/help'", (STATIC/'sidebar.js').read_text(encoding='utf-8'))

    def test_script_metadata_separated_from_scene_panel(self):
        html = (STATIC/'script-editor.html').read_text(encoding='utf-8')
        # Phase 2.5.1.4 split the page into two independent boxes (#editor-meta, #editor-scenes);
        # the older single-container markers (sceneHeading, editor.append…) no longer exist.
        self.assertIn('script-meta-panel', html)
        meta = html.index('id="editor-meta"')
        scenes = html.index('id="editor-scenes"')
        self.assertLess(meta, scenes)
        self.assertIn('</section>', html[meta:scenes], 'metadata and scenes must be separate boxes')
        self.assertIn('class="scene-nav"', html[scenes:])
        for field in ('title','language','style','duration','summary','script'):
            self.assertIn(f'id="{field}"', html)

    def test_batch_buttons_between_limits_and_prompts(self):
        source = (STATIC/'asset-workspace.html').read_text(encoding='utf-8')
        positions = [source.index('data-section="3"'), source.index('class="box batch-control-panel"'), source.index('data-section="4"')]
        self.assertEqual(sorted(positions),positions)
        batch = source[positions[1]:positions[2]]
        prompts = source[positions[2]:]
        for control in ('batchImage','batchAudio','voiceChoice'):
            self.assertIn(f'id="{control}"',batch)
            self.assertNotIn(f'id="{control}"',prompts)
        self.assertIn('id="scene-list"',prompts)
        self.assertIn('id="selectall"',prompts)

    def test_help_route_serves_html_when_ui_installed(self):
        try:
            from fastapi.testclient import TestClient
            from ai_video_editor.api.app import create_app
        except (ImportError, RuntimeError):
            self.skipTest('UI test dependencies not installed')
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            app=create_app(projects_dir=Path(tmp)/'projects',
                           default_config=ROOT/'config/default_project.json')
            with TestClient(app, base_url='http://localhost') as client:
                response=client.get('/help')
                self.assertEqual(response.status_code,200)
                self.assertIn('Hướng dẫn sử dụng AI Video Editor',response.text)

    def test_help_route_bound(self):
        source = (ROOT/'src/ai_video_editor/api/app.py').read_text(encoding='utf-8')
        self.assertIn("@app.get('/help'", source)
        self.assertIn("return FileResponse(public / 'help.html')",source)

if __name__=='__main__':unittest.main()
