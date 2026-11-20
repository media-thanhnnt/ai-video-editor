"""Phase 1.2 — safe, deterministic local-media inspection.

No renderer, AI calls, or changes to project input files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .project import Project, ValidationError, atomic_write_json, ensure_inside

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
AUDIO_EXTENSIONS = {'.mp3', '.wav'}
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG'}
MAX_IMAGE_PIXELS = 120_000_000
HASH_CHUNK = 1024 * 1024
DEFAULT_REQUIRED_DISK_MULTIPLIER = 3


class AssetError(RuntimeError):
    """Media missing, invalid, inaccessible, or FFmpeg unavailable."""


def _run(args: list[str], timeout: float = 120) -> str:
    try:
        process = subprocess.run(args, capture_output=True, timeout=timeout,
                                 encoding='utf-8', errors='replace', check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssetError(f'Command failed: {args[0]}: {exc}') from exc
    if process.returncode:
        raise AssetError(f'{args[0]} exited {process.returncode}: {process.stderr[-700:]}')
    return process.stdout


def _check_regular_file(path: Path, root: Path) -> None:
    # Resolve again immediately before opening: symlinks may have changed since storyboard validation.
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AssetError(f'Cannot resolve media: {path.name}: {exc}') from exc
    if not real.is_relative_to(root.resolve()):
        raise AssetError('Media resolves outside project directory')
    if not real.is_file():
        raise AssetError('Media is not a regular file')
    if real.stat().st_size == 0:
        raise AssetError('Empty media file')


def scan_assets(project: Project) -> dict[str, list[str]]:
    """Walk media directories, returning safe relative paths, sorted consistently."""
    result: dict[str, list[str]] = {}
    for category in ('images', 'audio', 'sequences', 'videos', 'subtitles', 'music', 'fonts'):
        folder = project.folder / 'assets' / category
        found: list[str] = []
        if folder.exists():
            for directory, names, filenames in os.walk(folder, followlinks=False):
                names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
                for filename in filenames:
                    file = Path(directory) / filename
                    if file.is_symlink():
                        continue
                    if file.is_file():
                        found.append(file.relative_to(project.folder).as_posix())
        result[category] = sorted(found)
    return result


def get_image_info(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise AssetError(f'Unsupported actual image format: {image.format}')
            image_format = image.format
            raw_size = list(image.size)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise AssetError('Image exceeds pixel safety limit')
            image.verify()  # Must run before getexif(): PNG verify requires untouched stream.
        with Image.open(path) as image:
            orientation = int(image.getexif().get(274, 1))
            image.load()  # Explicit pixel decode; verify() alone is not sufficient.
            corrected = ImageOps.exif_transpose(image)
            width, height = corrected.size
        if width <= 0 or height <= 0:
            raise AssetError('Invalid image dimensions')
        return {'format': image_format, 'raw_dimensions': raw_size, 'width': width,
                'height': height, 'aspect_ratio': round(width / height, 8),
                'exif_orientation': orientation, 'exif_transpose_applied': orientation not in (0, 1)}
    except (OSError, ValueError, UnidentifiedImageError, SyntaxError, Image.DecompressionBombError) as exc:
        raise AssetError(f'Image cannot be decoded: {exc}') from exc


def _probe_streams(path: Path) -> dict[str, Any]:
    raw = _run(['ffprobe', '-v', 'error', '-show_entries',
                'format=format_name,duration:stream=index,codec_type,codec_name,sample_rate,channels,duration',
                '-of', 'json', str(path)])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssetError(f'ffprobe returned invalid JSON: {exc}') from exc


def _decoded_sample_count(path: Path, sample_rate: int) -> int:
    """Count actual decoded PCM samples, resampled to project sample rate.

    Writes PCM to a temporary file and deletes it; no accumulating samples in memory.
    """
    with tempfile.TemporaryDirectory(prefix='aive-audio-') as temp:
        raw = Path(temp) / 'decoded.pcm'
        _run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-y', '-i', str(path),
              '-map', '0:a:0', '-vn', '-ar', str(sample_rate), '-ac', '1',
              '-c:a', 'pcm_s16le', '-f', 's16le', str(raw)], timeout=300)
        if not raw.exists() or raw.stat().st_size % 2:
            raise AssetError('Invalid decoded PCM output')
        return raw.stat().st_size // 2


def get_audio_info(path: Path, *, target_sample_rate: int = 48000) -> dict[str, Any]:
    if target_sample_rate <= 0:
        raise AssetError('Invalid target sample rate')
    data = _probe_streams(path)
    streams = data.get('streams', [])
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if len(audio) != 1 or any(s.get('codec_type') == 'video' for s in streams):
        raise AssetError('Expected exactly one audio stream and no video streams')
    stream = audio[0]
    codec = stream.get('codec_name')
    if path.suffix.lower() == '.mp3' and codec != 'mp3':
        raise AssetError(f'File extension .mp3 but codec={codec!r}')
    if path.suffix.lower() == '.wav' and codec not in ('pcm_s16le', 'pcm_s24le', 'pcm_s32le',
                                                     'pcm_f32le', 'pcm_f64le', 'pcm_u8'):
        raise AssetError(f'Unsupported WAV codec={codec!r}')
    try:
        sample_rate = int(stream['sample_rate'])
        channels = int(stream['channels'])
    except (KeyError, ValueError, TypeError) as exc:
        raise AssetError('Missing or invalid audio properties') from exc
    if sample_rate <= 0 or not 1 <= channels <= 8:
        raise AssetError('Invalid sample rate or channel count')
    samples = _decoded_sample_count(path, target_sample_rate)
    if samples <= 0:
        raise AssetError('Audio decodes to zero samples')
    return {'codec': codec, 'source_sample_rate': sample_rate, 'source_channels': channels,
            'timeline_sample_rate': target_sample_rate, 'decoded_samples': samples,
            'duration_seconds': round(samples / target_sample_rate, 9),
            'duration_source': 'decoded_pcm_samples_after_resampling'}


def compute_input_hashes(paths: dict[str, Path]) -> dict[str, str]:
    results: dict[str, str] = {}
    for key, path in sorted(paths.items()):
        digest = hashlib.sha256()
        with path.open('rb') as file:
            for chunk in iter(lambda: file.read(HASH_CHUNK), b''):
                digest.update(chunk)
        results[key] = 'sha256:' + digest.hexdigest()
    return results


def check_disk_space(project: Project, *, input_bytes: int, multiplier: int = DEFAULT_REQUIRED_DISK_MULTIPLIER) -> dict[str, Any]:
    if input_bytes < 0 or multiplier < 1:
        raise ValueError('Invalid disk estimate inputs')
    disk = shutil.disk_usage(project.folder)
    estimated = max(256 * 1024 * 1024, input_bytes * multiplier)
    return {'free_bytes': disk.free, 'estimated_required_bytes': estimated,
            'estimate_method': f'max(256MiB,input_bytes*{multiplier})',
            'sufficient': disk.free >= estimated,
            'note': 'Heuristic only; Phase 1.5 will estimate intermediate frame storage and output bitrate.'}


def _media_path(project: Project, relative: str, *, kind: str) -> Path:
    if not relative.startswith(f'assets/{kind}/'):
        raise AssetError(f'Media must be stored under assets/{kind}/')
    return ensure_inside(project.folder, relative)


def get_video_info(path: Path, *, fps: Fraction) -> dict[str, Any]:
    raw = _run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                'stream=codec_name,width,height,avg_frame_rate,duration,nb_frames','-of','json',str(path)])
    data = json.loads(raw)
    videos = data.get('streams',[])
    if len(videos) != 1 or not videos[0].get('codec_name'):
        raise AssetError('Video clip has no decodable video stream')
    stream = videos[0]
    try:
        duration = float(stream['duration'])
    except (KeyError, ValueError, TypeError):
        # Prefer actual video packet count, not the container's (possibly longer) audio duration.
        counted = json.loads(_run(['ffprobe','-v','error','-select_streams','v:0',
                                   '-count_packets','-show_entries',
                                   'stream=nb_read_packets,avg_frame_rate','-of','json',str(path)]))
        try:
            video = counted['streams'][0]
            rate = Fraction(video['avg_frame_rate'])
            duration = int(video['nb_read_packets']) / float(rate)
        except (IndexError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            raise AssetError('Video stream duration unavailable') from exc
    if not 0 < duration <= 36000:
        raise AssetError('Video clip duration invalid or too long')
    width, height = int(videos[0]['width']), int(videos[0]['height'])
    if width < 2 or height < 2 or width*height > MAX_IMAGE_PIXELS:
        raise AssetError('Video clip dimensions invalid')
    # Normalize all clips to project FPS, duration rounded to nearest output frame.
    frames = max(1, round(duration * fps))
    # Probe all streams: video sources can contain AAC/other audio, or be silent.
    stream_data = _probe_streams(path)
    has_audio = any(stream.get('codec_type') == 'audio' for stream in stream_data.get('streams', []))
    return {'codec':videos[0]['codec_name'],'width':width,'height':height,
            'duration_seconds':duration,'target_frames':frames,'target_fps':str(fps),
            'has_audio':has_audio}


def sequence_frame_paths(scene: dict[str, Any]) -> list[str]:
    """Expand only the explicitly validated contiguous 1-based sequence."""
    seq = scene['sequence']
    return [seq['frames_dir'] + '/' + (seq['frame_pattern'] % i)
            for i in range(1, seq['frame_count'] + 1)]


def match_scene_assets(project: Project) -> list[dict[str, Any]]:
    result = []
    for scene in project.storyboard['scenes']:
        if scene.get('type', 'image') == 'image_sequence':
            result.append({'scene_id':scene['scene_id'], 'type':'image_sequence',
                           'frames':sequence_frame_paths(scene), 'audio':scene.get('audio')})
        elif scene.get('type') == 'video_clip':
            result.append({'scene_id':scene['scene_id'], 'type':'video_clip',
                           'video':scene['video'], 'audio':scene.get('audio')})
        else:
            result.append({'scene_id':scene['scene_id'], 'type':'image',
                           'image':scene['image'], 'audio':scene['audio']})
    return result


def validate_assets(project: Project, *, write_report: bool = True) -> dict[str, Any]:
    """Return structured per-scene results; any failure returns overall FAIL.

    Each file is processed once, even if referenced by multiple scenes.
    """
    scanned = scan_assets(project)
    file_results: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    scenes: list[dict[str, Any]] = []
    total_size = 0
    references: dict[str, str] = {}
    for scene in match_scene_assets(project):
        if scene['type'] == 'image':
            references.setdefault(scene['image'], 'image')
        elif scene['type'] == 'video_clip':
            references.setdefault(scene['video'], 'video')
        else:
            for relative in scene['frames']:
                references.setdefault(relative, 'image')
        if scene.get('audio'):
            references.setdefault(scene['audio'], 'audio')
    for track in project.storyboard.get('audio_tracks', []):
        references.setdefault(track['file'], 'audio')
    if project.storyboard.get('subtitle_file'):
        references.setdefault(project.storyboard['subtitle_file'],'subtitle')
    for relative, kind in sorted(references.items()):
        item: dict[str, Any] = {'status': 'FAIL', 'type': kind}
        try:
            path = _media_path(project, relative, kind=('sequences' if relative.startswith('assets/sequences/') else
                                                               'images' if kind == 'image' else
                                                               'videos' if kind == 'video' else
                                                               'subtitles' if kind == 'subtitle' else 'audio'))
            if path.suffix.lower() not in (IMAGE_EXTENSIONS if kind == 'image' else AUDIO_EXTENSIONS if kind == 'audio' else {'.mp4','.mov','.mkv'} if kind == 'video' else {'.srt'}):
                raise AssetError(f'Unsupported {kind} extension: {path.suffix}')
            _check_regular_file(path, project.folder)
            size = path.stat().st_size
            info = (get_image_info(path) if kind == 'image' else
                    get_audio_info(path, target_sample_rate=project.config['output']['audio_sample_rate']) if kind == 'audio' else
                    get_video_info(path, fps=Fraction(project.config['output']['fps'])) if kind == 'video' else
                    {'format':'srt','bytes':len(path.read_bytes())})
            digest = compute_input_hashes({relative: path})[relative]
            item.update(status='PASS', size_bytes=size, info=info, sha256=digest)
            hashes[relative] = digest
            total_size += size
        except (AssetError, ValidationError, OSError) as exc:
            item['error'] = str(exc)
        file_results[relative] = item
    for scene in match_scene_assets(project):
        base_paths = ([scene['image']] if scene['type']=='image' else
                      [scene['video']] if scene['type']=='video_clip' else scene['frames'])
        paths = [*base_paths, scene['audio']] if scene.get('audio') else list(base_paths)
        errors = [f'{relative}: {file_results[relative].get("error", "invalid")}'
                  for relative in paths if file_results[relative]['status'] != 'PASS']
        if scene['type'] == 'video_clip' and not errors:
            source = next((item for item in project.storyboard['scenes'] if item['scene_id'] == scene['scene_id']), None)
            audio_setting = source.get('source_audio', {}) if source else {}
            if audio_setting.get('enabled', True) and not file_results[scene['video']]['info']['has_audio'] and audio_setting.get('enabled') is True:
                errors.append('source_audio.enabled=true but video has no audio stream')
        # Prevent frame-to-frame geometry mismatch from being silently normalized.
        if scene['type'] == 'image_sequence' and not errors:
            dimensions = {(file_results[path]['info']['width'], file_results[path]['info']['height'])
                          for path in scene['frames']}
            if len(dimensions) != 1:
                errors.append('Sequence frames must all share the same dimensions')
        scenes.append({'scene_id':scene['scene_id'], 'status':'FAIL' if errors else 'PASS',
                       'type':scene['type'], 'image':scene.get('image'),
                       'audio':scene.get('audio'), 'frame_count':len(scene.get('frames', [])),
                       'errors':errors})
    for relative in [t['file'] for t in project.storyboard.get('audio_tracks', [])] + ([project.storyboard['subtitle_file']] if project.storyboard.get('subtitle_file') else []):
        if file_results[relative]['status'] != 'PASS':
            scenes.append({'scene_id':'GLOBAL', 'status':'FAIL','errors':[file_results[relative].get('error','invalid')]})
    disk = check_disk_space(project, input_bytes=total_size)
    config_path = project.folder / 'config.json'
    storyboard_path = project.folder / 'storyboard.json'
    hashes.update(compute_input_hashes({'config.json': config_path, 'storyboard.json': storyboard_path}))
    overall = ('FAIL' if not scenes or any(s['status'] == 'FAIL' for s in scenes) or not disk['sufficient']
               else 'PASS')
    report = {'schema_version': '1.1', 'phase': '1.2', 'project_id': project.project_id,
              'generated_utc': datetime.now(timezone.utc).isoformat(), 'status': overall,
              'scene_count': len(scenes), 'scanned_assets': scanned, 'scene_results': scenes,
              'file_results': file_results, 'input_hashes': hashes, 'disk': disk,
              'warnings': (['Project has no scenes'] if not scenes else [])}
    if write_report:
        atomic_write_json(project.folder / 'reports' / 'assets_report.json', report)
    return report
