"""Phase 2.5.1.7: overlay layer, auto fit, Gemini voices and wired-up config settings.

No provider/network access. FFmpeg tests skip when FFmpeg is unavailable.
"""
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from ai_video_editor import renderer
from ai_video_editor.api.voice_settings import GEMINI_VOICES, options, validate_voice
from ai_video_editor.audio import duck_filter, measure_loudness_gain, mux_project_audio, wrap_cue, _paged_cues
from ai_video_editor.assets import validate_assets
from ai_video_editor.overlay import card_blocks, render_card, split_prompt, _Fonts, generate_overlays
from ai_video_editor.planner import get_default_editing_plan
from ai_video_editor.project import ProjectManager, ValidationError, validate_config, validate_storyboard, atomic_write_json
from ai_video_editor.qa import qa_project
from ai_video_editor.sequence import fit_filter
from ai_video_editor.timeline import build_timeline

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
WORD = ('Minh họa nhân viên. TEXT OVERLAY: 上司（じょうし）; Romaji: jōshi; Nghĩa: cấp trên — supervisor; '
        'Ghi chú: A; B.; 上司に報告します。; Romaji: Jōshi ni hōkoku shimasu.; English: I report.; Tiếng Việt: Tôi báo cáo.')


def _tone(path, seconds, freq, amplitude, rate=48000):
    frames = int(seconds * rate)
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(b''.join(struct.pack('<h', int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)))
                                 for i in range(frames)))


def _fonts_or_skip(test):
    try:
        return _Fonts(None)
    except ValidationError:
        test.skipTest('No system font available for overlay rendering')


class VoiceAndConfigTests(unittest.TestCase):
    def test_all_30_gemini_voices(self):
        self.assertEqual(len(GEMINI_VOICES), 30)
        self.assertEqual(len(set(GEMINI_VOICES)), 30)
        self.assertEqual(options('gemini', 'gemini')[:6], ['Kore', 'Puck', 'Charon', 'Fenrir', 'Aoede', 'Zephyr'])
        self.assertEqual(validate_voice('Sulafat', 'gemini', 'gemini'), 'Sulafat')

    def test_config_validation_of_new_settings(self):
        base = json.loads((ROOT / 'config/default_project.json').read_text(encoding='utf-8'))
        validate_config(json.loads(json.dumps(base)))
        for section, key, bad in (('image', 'fit_mode', 'stretch'), ('output', 'quality', 'ultra'),
                                  ('motion', 'easing', 'bounce'), ('audio', 'duck_depth_db', 5),
                                  ('audio', 'loudness_target_lufs', -80), ('subtitles', 'max_lines', 0),
                                  ('retry', 'render_max_attempts', 9), ('planning', 'use_ai', 'yes')):
            cfg = json.loads(json.dumps(base)); cfg[section][key] = bad
            with self.assertRaises(ValidationError, msg=f'{section}.{key}'):
                validate_config(cfg)
        legacy = json.loads(json.dumps(base))
        for section, key in (('audio', 'duck_depth_db'), ('motion', 'easing'), ('retry', 'render_max_attempts')):
            legacy[section].pop(key)
        validate_config(legacy)  # missing optional keys keep the historical behaviour

    def test_default_settings_keep_existing_cache_keys(self):
        cfg = json.loads((ROOT / 'config/default_project.json').read_text(encoding='utf-8'))
        self.assertEqual(renderer._motion_key(cfg), {})
        self.assertEqual(renderer._motion_key(cfg, 'medium'), {})
        self.assertEqual(renderer._crf(cfg), 18)
        cfg['motion']['zoom_factor']['medium'] = 1.2
        self.assertEqual(renderer._motion_key(cfg, 'medium'), {'zoom': 1.2, 'easing': 'ease_in_out'})

    def test_zoom_factor_and_easing_change_motion(self):
        source = np.zeros((90, 160, 3), np.uint8); source[:, :80] = 255
        default = renderer.motion_frame(source, 'zoom_in', 'medium', 5, 10, 160, 90)
        stronger = renderer.motion_frame(source, 'zoom_in', 'medium', 5, 10, 160, 90,
                                         zoom_factors={'subtle': 1.05, 'medium': 1.4, 'strong': 1.18})
        linear = renderer.motion_frame(source, 'zoom_in', 'medium', 2, 10, 160, 90, easing='linear')
        eased = renderer.motion_frame(source, 'zoom_in', 'medium', 2, 10, 160, 90)
        self.assertFalse(np.array_equal(default, stronger))
        self.assertFalse(np.array_equal(linear, eased))


class FitTests(unittest.TestCase):
    def test_auto_fit_choice(self):
        self.assertEqual(renderer._resolve_fit('auto', 1920, 1080, 1920, 1080), 'cover')
        self.assertEqual(renderer._resolve_fit('auto', 1536, 1024, 1920, 1080), 'blur_fill')
        self.assertEqual(renderer._resolve_fit('auto', 1024, 1024, 1920, 1080), 'blur_fill')
        self.assertEqual(renderer._resolve_fit('cover', 1536, 1024, 1920, 1080), 'cover')

    def test_auto_keeps_whole_three_by_two_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'img.png'
            image = Image.new('RGB', (300, 200), 'blue')
            image.paste(Image.new('RGB', (300, 10), 'red'), (0, 0))  # red strip at the very top
            image.save(path)
            cover = renderer._prepare_source(path, 320, 180, 1, 'cover')
            auto = renderer._prepare_source(path, 320, 180, 1, 'auto')
        red = lambda a: int(((a[..., 0] > 200) & (a[..., 2] < 80)).sum())
        self.assertEqual(red(cover), 0, 'cover crops the top strip away')
        self.assertGreater(red(auto), 0, 'auto/blur_fill keeps the full image')

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg unavailable')
    def test_blur_fill_filter_runs_for_video_and_sequence(self):
        for chain in ('fps=30/1,' + fit_filter(320, 180, 'blur_fill') + ',tpad=stop_mode=clone:stop=-1,format=bgr0',
                      fit_filter(320, 180, 'blur_fill') + ',format=bgr0',
                      fit_filter(320, 180, 'auto') + ',format=bgr0'):
            proc = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
                                   '-i', 'testsrc=size=200x200:rate=30:duration=0.2', '-vf', chain,
                                   '-frames:v', '3', '-f', 'null', '-'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class SubtitleAndDuckTests(unittest.TestCase):
    def test_wrap_and_page_long_cue(self):
        pages = wrap_cue('one two three four five six seven eight nine ten', 10, 2)
        self.assertTrue(all(len(line) <= 10 for page in pages for line in page.split('\n')))
        self.assertTrue(all(len(page.split('\n')) <= 2 for page in pages))
        self.assertEqual(' '.join(' '.join(p.split('\n')) for p in pages),
                         'one two three four five six seven eight nine ten')
        timed = _paged_cues({'start_seconds': 1.0, 'end_seconds': 4.0, 'text': 'aaaa bbbb cccc dddd eeee ffff'}, 9, 1)
        self.assertEqual(timed[0][0], 1.0)
        self.assertEqual(timed[-1][1], 4.0)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(timed, timed[1:])))
        self.assertEqual(wrap_cue('上司に報告します', 4, 2), ['上司に報\n告します'])

    def test_duck_filter_merges_close_voices(self):
        timeline = {'voice_track': [{'start_sample': 48000, 'end_sample': 96000},
                                    {'start_sample': 100000, 'end_sample': 140000},
                                    {'start_sample': 400000, 'end_sample': 450000}]}
        expression = duck_filter(timeline, 48000, {'duck_depth_db': -10, 'duck_attack_ms': 150, 'duck_release_ms': 400})
        self.assertTrue(expression.startswith("volume='pow(10,-10.000*("))
        self.assertEqual(expression.count('min('), 2)  # first two voices merged
        self.assertIsNone(duck_filter(timeline, 48000, {'duck_depth_db': 0}))


class OverlayTests(unittest.TestCase):
    def test_split_prompt_hides_overlay_from_image_models(self):
        illustration, text = split_prompt(WORD)
        self.assertEqual(illustration, 'Minh họa nhân viên.')
        self.assertTrue(text.startswith('上司（じょうし）'))
        self.assertEqual(split_prompt('Just a picture'), ('Just a picture', None))

    def test_card_kinds(self):
        word = card_blocks(split_prompt(WORD)[1], 'Từ số 4. 上司 …')
        texts = [b[1] for b in word if b[0] in ('text', 'big', 'note')]
        self.assertIn('TỪ SỐ 04', texts)
        self.assertIn('上司', texts)
        self.assertIn('Ghi chú: A; B.', texts)  # note containing "; " stays one note
        quiz = card_blocks('Thẻ 1: 仕様 → đặc tả; Thẻ 2: 納期 → thời hạn')
        self.assertIn('1.  仕様', [b[1] for b in quiz if b[0] == 'text'])
        self.assertNotIn('đặc tả', json.dumps(quiz, ensure_ascii=False))  # answers hidden
        self.assertEqual(card_blocks('30 Từ IT; Nhật – Anh – Việt')[0][1], '30 Từ IT')

    def test_render_card_is_transparent_layer(self):
        fonts = _fonts_or_skip(self)
        layer = render_card(card_blocks(split_prompt(WORD)[1]), 1920, 1080, fonts)
        self.assertEqual((layer.mode, layer.size), ('RGBA', (1920, 1080)))
        self.assertEqual(layer.getpixel((1700, 540))[3], 0, 'right side stays see-through')
        self.assertGreater(layer.getpixel((500, 540))[3], 200, 'panel is opaque')
        portrait = render_card(card_blocks('Title; Subtitle'), 1080, 1920, fonts)
        self.assertEqual(portrait.getpixel((540, 200))[3], 0, 'portrait panel sits at the bottom')

    def test_storyboard_overlay_rules(self):
        scene = {'scene_id': '001', 'order': 1, 'type': 'image', 'narration': 'x',
                 'image': 'assets/images/a.png', 'audio': 'assets/audio/a.wav', 'overlay': 'assets/overlays/a.png'}
        board = {'schema_version': '1.1', 'project_id': 'demo', 'title': 'T', 'voice_mode': 'per_scene', 'scenes': [scene]}
        validate_storyboard(board)
        for bad in ('assets/images/a.png', 'assets/overlays/a.jpg', '../a.png'):
            with self.assertRaises(ValidationError):
                validate_storyboard({**board, 'scenes': [{**scene, 'overlay': bad}]})
        clip = {'scene_id': '001', 'order': 1, 'type': 'video_clip', 'narration': '',
                'video': 'assets/videos/a.mp4', 'overlay': 'assets/overlays/a.png'}
        with self.assertRaises(ValidationError):
            validate_storyboard({**board, 'scenes': [clip]})

    def test_generate_bind_and_compile_overlay(self):
        _fonts_or_skip(self)
        from ai_video_editor.agent.manifest import create_manifest, approve_manifest, compile_storyboard
        with tempfile.TemporaryDirectory() as tmp:
            pm = ProjectManager(Path(tmp) / 'projects', ROOT / 'config/default_project.json')
            project = pm.create_project('demo', 'Demo')
            atomic_write_json(project.folder / 'agent_script_draft.json', {
                'schema_version': '2.2', 'project_id': 'demo', 'status': 'AWAITING_SCRIPT_APPROVAL',
                'source': 'manual', 'requirement': '', 'approved_hash': None,
                'content': {'title': 'Demo', 'language': 'vi', 'style': 'calm', 'target_duration_seconds': 6,
                            'summary': 's', 'script': 'a b',
                            'scenes': [{'scene_id': '001', 'type': 'image', 'narration': 'Từ số 4. 上司',
                                        'prompt': WORD, 'estimated_duration_seconds': 3},
                                       {'scene_id': '002', 'type': 'image', 'narration': 'b',
                                        'prompt': 'No overlay here', 'estimated_duration_seconds': 3}]},
                'usage': {'provider': 'manual', 'input_tokens': 0, 'output_tokens': 0,
                          'total_tokens': 0, 'estimated_cost_usd': 0}})
            create_manifest(project, {'title': 'Demo', 'scenes': [
                {'scene_id': sid, 'type': 'image', 'narration': 'x', 'prompt': 'p'} for sid in ('001', '002')]})
            from ai_video_editor.agent.manifest import bind_asset
            for sid in ('001', '002'):
                Image.new('RGB', (32, 18)).save(project.folder / f'assets/images/{sid}.png')
                _tone(project.folder / f'assets/audio/{sid}.wav', 0.1, 440, 0.1)
                bind_asset(project, sid, 'media', f'assets/images/{sid}.png')
                bind_asset(project, sid, 'voice', f'assets/audio/{sid}.wav')
            result = generate_overlays(project)
            self.assertEqual([c['scene_id'] for c in result['created']], ['001'])
            self.assertEqual(result['skipped'], ['002'])
            self.assertEqual(result['bound'], ['001'])
            with self.assertRaises(ValidationError):
                generate_overlays(project, scene_ids=['999'])
            approve_manifest(project)
            board = compile_storyboard(project)
            self.assertEqual(board['scenes'][0]['overlay'], result['created'][0]['file'])
            self.assertNotIn('overlay', board['scenes'][1])
            from ai_video_editor.agent.asset_workflow import make_prompt_pack
            self.assertNotIn('TEXT OVERLAY', make_prompt_pack(project)['scenes'][0]['image_prompt'])


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg unavailable')
class EndToEndTests(unittest.TestCase):
    def test_overlay_auto_fit_ducking_loudness_and_retry(self):
        fonts = _fonts_or_skip(self)
        with tempfile.TemporaryDirectory(prefix='phase2517-') as tmp:
            pm = ProjectManager(Path(tmp) / 'projects', ROOT / 'config/default_project.json')
            project = pm.create_project('demo', 'E2E')
            config = project.config
            config['planning']['require_plan_approval'] = False
            config['output'].update(width=320, height=180, encoder_preset='ultrafast')
            config['motion']['supersample_factor'] = 1
            config['transitions'].update(intro='none', outro='none')
            config['retry'].update(render_max_attempts=2, backoff_seconds=[0])
            board = project.storyboard
            board['scenes'] = [{'scene_id': '001', 'order': 1, 'narration': 'Xin chào',
                                'image': 'assets/images/a.png', 'audio': 'assets/audio/voice.wav',
                                'overlay': 'assets/overlays/a.png'}]
            board['audio_tracks'] = [{'file': 'assets/audio/music.wav', 'start_seconds': 0, 'volume': 0.5}]
            project = pm.save_project('demo', config=config, storyboard=board)
            folder = project.folder
            Image.new('RGB', (300, 200), (0, 90, 220)).save(folder / 'assets/images/a.png')  # 3:2 → blur_fill
            render_card(card_blocks(split_prompt(WORD)[1]), 320, 180, fonts).save(folder / 'assets/overlays/a.png')
            _tone(folder / 'assets/audio/voice.wav', 1.0, 440, 0.05)   # quiet voice → loudness gain > 0
            _tone(folder / 'assets/audio/music.wav', 3.0, 220, 0.3)
            assets = validate_assets(project)
            self.assertEqual(assets['status'], 'PASS', assets)
            plan = get_default_editing_plan(project, style='calm', seed=1)
            build_timeline(project, asset_report=assets, editing_plan=plan)
            original = renderer._render_project_locked
            calls = []
            def flaky(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise renderer.RenderError('transient encoder failure')
                return original(*args, **kwargs)
            with patch.object(renderer, '_render_project_locked', side_effect=flaky):
                renderer.render_project(project)
            self.assertEqual(len(calls), 2, 'retried once')
            report = mux_project_audio(project)
            self.assertTrue(report['ducking'])
            self.assertGreater(report['loudness']['gain_db'], 0)
            self.assertEqual(qa_project(project)['status'], 'PASS')
            frame = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-i',
                                    str(folder / 'output/final.mp4'), '-vf', 'select=eq(n\\,10)', '-frames:v', '1',
                                    '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
            pixels = np.frombuffer(frame, np.uint8).reshape(180, 320, 3)
            self.assertGreater(int(pixels[160, 40].mean()), 200, 'overlay panel is drawn (white)')
            self.assertLess(int(pixels[90, 300, 0]), 120, 'right side still shows the blue illustration')


if __name__ == '__main__':
    unittest.main()
