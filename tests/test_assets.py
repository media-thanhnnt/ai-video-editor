import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from ai_video_editor.assets import (AssetError, scan_assets, get_image_info, get_audio_info,
                                    compute_input_hashes, check_disk_space, validate_assets)
from ai_video_editor.project import ProjectManager, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def write_wav(path, *, seconds=0.25):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b'\x00\x00' * round(seconds * 48000))


class AssetManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pm = ProjectManager(Path(self.temp.name) / 'projects', ROOT / 'config/default_project.json')
        self.proj = self.pm.create_project('vid001', 'Audio and Images')
        folder = self.proj.folder
        Image.new('RGB', (120, 68), 'blue').save(folder / 'assets/images/scene_001.png')
        write_wav(folder / 'assets/audio/scene_001.wav')
        storyboard = copy.deepcopy(self.proj.storyboard)
        storyboard['scenes'] = [{'scene_id': '001', 'order': 1, 'narration': 'Hello',
                                 'image': 'assets/images/scene_001.png',
                                 'audio': 'assets/audio/scene_001.wav'}]
        self.proj = self.pm.save_project('vid001', storyboard=storyboard)

    def test_scan(self):
        listing = scan_assets(self.proj)
        self.assertEqual(listing['images'], ['assets/images/scene_001.png'])
        self.assertEqual(listing['audio'], ['assets/audio/scene_001.wav'])

    def test_image_dimensions(self):
        info = get_image_info(self.proj.folder / 'assets/images/scene_001.png')
        self.assertEqual((info['width'], info['height']), (120, 68))

    def test_wav_decoded_samples(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('Requires FFmpeg tools')
        info = get_audio_info(self.proj.folder / 'assets/audio/scene_001.wav')
        self.assertEqual(info['decoded_samples'], 12000)

    def test_valid_project(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('Requires FFmpeg tools')
        report = validate_assets(self.proj)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['scene_results'][0]['status'], 'PASS')
        self.assertEqual(len(report['input_hashes']), 4)
        self.assertTrue((self.proj.folder / 'reports/assets_report.json').exists())

    def test_missing_media(self):
        (self.proj.folder / 'assets/images/scene_001.png').unlink()
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('image', report['scene_results'][0]['errors'][0])

    def test_corrupt_image(self):
        (self.proj.folder / 'assets/images/scene_001.png').write_bytes(b'fake png data')
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'FAIL')

    def test_zero_length_audio(self):
        (self.proj.folder / 'assets/audio/scene_001.wav').write_bytes(b'')
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'FAIL')

    def test_wrong_extension(self):
        storyboard = copy.deepcopy(self.proj.storyboard)
        storyboard['scenes'][0]['audio'] = 'assets/audio/scene_001.txt'
        self.proj = self.pm.save_project('vid001', storyboard=storyboard)
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'FAIL')

    def test_reject_symlink_outside(self):
        outside = Path(self.temp.name) / 'external.png'
        Image.new('RGB', (16, 16)).save(outside)
        link = self.proj.folder / 'assets/images/scene_001.png'
        link.unlink()
        try:
            link.symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest('Symlink creation not permitted')
        with self.assertRaises(ValidationError):
            self.pm.load_project('vid001')

    def test_same_bytes_same_hash(self):
        path = self.proj.folder / 'assets/images/scene_001.png'
        self.assertEqual(compute_input_hashes({'a': path})['a'], compute_input_hashes({'a': path})['a'])

    def test_hash_changes_on_edit(self):
        path = self.proj.folder / 'assets/images/scene_001.png'
        old = compute_input_hashes({'a': path})
        Image.new('RGB', (120, 68), 'red').save(path)
        self.assertNotEqual(old, compute_input_hashes({'a': path}))

    def test_disk_estimate(self):
        disk = check_disk_space(self.proj, input_bytes=1024)
        self.assertGreater(disk['estimated_required_bytes'], 0)

    def test_empty_storyboard_not_ready(self):
        storyboard = copy.deepcopy(self.proj.storyboard)
        storyboard['scenes'] = []
        self.proj = self.pm.save_project('vid001', storyboard=storyboard)
        self.assertEqual(validate_assets(self.proj, write_report=False)['status'], 'FAIL')

    def test_repeat_media_paths_cached(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('Requires FFmpeg tools')
        storyboard = copy.deepcopy(self.proj.storyboard)
        scene2 = dict(storyboard['scenes'][0], scene_id='002', order=2)
        storyboard['scenes'].append(scene2)
        self.proj = self.pm.save_project('vid001', storyboard=storyboard)
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(len(report['file_results']), 2)
        self.assertEqual(report['scene_count'], 2)

    def test_mp3_codec_and_duration(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('Requires FFmpeg tools')
        source = self.proj.folder / 'assets/audio/scene_001.wav'
        target = self.proj.folder / 'assets/audio/scene_001.mp3'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-c:a', 'libmp3lame', str(target)], check=True)
        info = get_audio_info(target)
        self.assertEqual(info['codec'], 'mp3')
        self.assertGreater(info['decoded_samples'], 0)

    def test_ten_scenes(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('Requires FFmpeg tools')
        storyboard = copy.deepcopy(self.proj.storyboard)
        storyboard['scenes'] = []
        for i in range(1, 11):
            sid = f'{i:03d}'
            if i > 1:
                Image.new('RGB', (120, 68), 'blue').save(self.proj.folder / f'assets/images/scene_{sid}.png')
                write_wav(self.proj.folder / f'assets/audio/scene_{sid}.wav')
            storyboard['scenes'].append({'scene_id': sid, 'order': i, 'narration': 'Test',
                'image': f'assets/images/scene_{sid}.png', 'audio': f'assets/audio/scene_{sid}.wav'})
        self.proj = self.pm.save_project('vid001', storyboard=storyboard)
        report = validate_assets(self.proj)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['scene_count'], 10)
        self.assertEqual(len(report['file_results']), 20)

    def test_damaged_wav_fails(self):
        path = self.proj.folder / 'assets/audio/scene_001.wav'
        path.write_bytes(b'RIFF' + b'\x00' * 40)
        report = validate_assets(self.proj, write_report=False)
        self.assertEqual(report['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
