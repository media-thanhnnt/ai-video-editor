"""Phase 1.7 unit tests for guarded resumable render helpers."""
import json
import os
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_video_editor.renderer import (_digest, _render_lock, _sha256_file,
                                      _use_cached_video, _checkpoint_write, RenderError)


class CheckpointTests(unittest.TestCase):
    def test_digest_is_order_independent(self):
        self.assertEqual(_digest({'b': 2, 'a': 1}), _digest({'a': 1, 'b': 2}))
        self.assertNotEqual(_digest({'a': 1}), _digest({'a': 2}))

    def test_file_hash_detects_corruption(self):
        with TemporaryDirectory() as temp:
            f = Path(temp) / 'x.mkv'
            f.write_bytes(b'good')
            original = _sha256_file(f)
            f.write_bytes(b'bad!')
            self.assertNotEqual(original, _sha256_file(f))

    def test_checkpoint_is_atomic_and_has_step(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / 'report.json'
            record = {'status': 'IN_PROGRESS'}
            _checkpoint_write(target, record, 'scene_001')
            loaded = json.loads(target.read_text())
            self.assertEqual(loaded['last_step'], 'scene_001')
            self.assertTrue(loaded['updated_utc'])

    def test_render_lock_prevents_double_render(self):
        with TemporaryDirectory() as temp:
            work = Path(temp)
            # On Windows os.kill(pid, 0) may deliver CTRL+C to the current
            # console group. The lock implementation must never call it.
            if os.name == 'nt':
                kill_guard = patch('ai_video_editor.renderer.os.kill', side_effect=AssertionError('Windows lock probing must not call os.kill'))
            else:
                from contextlib import nullcontext
                kill_guard = nullcontext()
            with kill_guard:
                with _render_lock(work):
                    with self.assertRaises(RenderError):
                        with _render_lock(work):
                            pass
            self.assertFalse((work / 'render.lock').exists())

    def test_render_lock_clears_dead_same_host_process(self):
        import socket
        with TemporaryDirectory() as temp:
            lock = Path(temp) / 'render.lock'
            lock.write_text(json.dumps({'pid': 99999999, 'host': socket.gethostname()}))
            with _render_lock(Path(temp)):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_missing_cache_not_accepted(self):
        with TemporaryDirectory() as temp:
            self.assertFalse(_use_cached_video(Path(temp) / 'missing.mkv',30,640,360,Fraction(30)))


if __name__ == '__main__':
    unittest.main()
