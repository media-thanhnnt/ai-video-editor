"""Phase 1.1: local project creation, storage and structural validation.

No FFmpeg execution, AI calls, media probing or timeline rendering here.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = '1.1'
PROJECT_ID = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$')
SCENE_ID = re.compile(r'^[A-Za-z0-9_]+$')


class ValidationError(ValueError):
    """Input JSON or project structure violates the Phase 1.1 contract."""


def ensure_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f'{label} must be a JSON object')
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{label} must be a non-empty string')
    return value


def check_relpath(value: Any, label: str) -> str:
    s = require_string(value, label)
    if ('\\' in s or ':' in s or s.startswith('/') or '\x00' in s or
        any(part in ('', '.', '..') for part in s.split('/'))):
        raise ValidationError(f'{label} must be a safe project-relative POSIX path: {s!r}')
    return s


def ensure_inside(base: Path, relative: str) -> Path:
    check_relpath(relative, 'path')
    base = base.resolve()
    candidate = (base / Path(*PurePosixPath(relative).parts)).resolve()
    if not candidate.is_relative_to(base):
        raise ValidationError(f'Path escapes project directory: {relative}')
    return candidate


def validate_config(raw: Any) -> dict[str, Any]:
    data = ensure_object(raw, 'config')
    if data.get('schema_version') != SCHEMA_VERSION:
        raise ValidationError(f'Unsupported config schema_version: {data.get("schema_version")!r}')
    for key in ('output', 'image', 'timing', 'transitions', 'motion', 'audio', 'subtitles', 'planning', 'retry'):
        ensure_object(data.get(key), f'config.{key}')
    output = data['output']
    for k in ('width', 'height', 'audio_sample_rate', 'audio_channels'):
        v = output.get(k)
        if type(v) is not int or v <= 0:
            raise ValidationError(f'config.output.{k} must be positive integer')
    if output['width'] % 2 or output['height'] % 2:
        raise ValidationError('H.264 output dimensions must be even')
    fps = require_string(output.get('fps'), 'config.output.fps')
    try:
        rate = Fraction(fps)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValidationError('config.output.fps must be rational, e.g. 30000/1001') from exc
    if '/' not in fps or rate <= 0:
        raise ValidationError('config.output.fps must be positive rational N/D')
    if output.get('video_codec') != 'h264' or output.get('audio_codec') != 'aac':
        raise ValidationError('Phase 1 requires video_codec=h264 and audio_codec=aac')
    if data['image'].get('fit_mode') not in ('cover','contain','blur_fill','auto'):
        raise ValidationError('Unsupported image.fit_mode')
    for k in ('pad_head_seconds','pad_tail_seconds','min_scene_seconds'):
        n = data['timing'].get(k)
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
            raise ValidationError(f'config.timing.{k} must be nonnegative finite number')
    if data['transitions'].get('violation_policy') not in ('clamp_and_warn','fail'):
        raise ValidationError('Invalid transition violation_policy')
    if data['planning'].get('style') not in ('documentary','calm','energetic'):
        raise ValidationError('Invalid planning.style')
    if data['subtitles'].get('mode') not in ('off','sidecar','burn_in','both'):
        raise ValidationError('Invalid subtitles.mode')
    check_relpath(data['subtitles'].get('font_file'), 'subtitles.font_file')
    _validate_optional_settings(data)
    return data


# Defaults for settings added after a config file was created. Every key is optional
# in config.json; a missing key behaves like these values (old projects keep working).
QUALITY_CRF = {'very_high': 15, 'high': 18, 'medium': 21, 'low': 24}
DEFAULT_ZOOM = {'subtle': 1.05, 'medium': 1.10, 'strong': 1.18}


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValidationError(f'{label} must be a number between {low} and {high}')
    return float(value)


def _validate_optional_settings(data: dict[str, Any]) -> None:
    if data['output'].get('quality', 'high') not in QUALITY_CRF:
        raise ValidationError('config.output.quality must be very_high, high, medium or low')
    motion = data['motion']
    zoom = motion.get('zoom_factor', DEFAULT_ZOOM)
    if not isinstance(zoom, dict) or set(zoom) != set(DEFAULT_ZOOM):
        raise ValidationError('config.motion.zoom_factor needs subtle, medium and strong')
    for name, value in zoom.items():
        _number(value, f'config.motion.zoom_factor.{name}', 1.0, 1.5)
    if motion.get('easing', 'ease_in_out') not in ('ease_in_out', 'linear'):
        raise ValidationError('config.motion.easing must be ease_in_out or linear')
    audio = data['audio']
    if type(audio.get('music_enabled', True)) is not bool:
        raise ValidationError('config.audio.music_enabled must be true or false')
    _number(audio.get('duck_depth_db', 0), 'config.audio.duck_depth_db', -40, 0)
    _number(audio.get('duck_attack_ms', 150), 'config.audio.duck_attack_ms', 0, 5000)
    _number(audio.get('duck_release_ms', 400), 'config.audio.duck_release_ms', 0, 5000)
    _number(audio.get('music_fade_in_seconds', 0), 'config.audio.music_fade_in_seconds', 0, 60)
    _number(audio.get('music_fade_out_seconds', 0), 'config.audio.music_fade_out_seconds', 0, 60)
    if audio.get('loudness_target_lufs') is not None:
        _number(audio['loudness_target_lufs'], 'config.audio.loudness_target_lufs', -36, -5)
    _number(audio.get('true_peak_max_dbtp', -1.0), 'config.audio.true_peak_max_dbtp', -24, 0)
    subtitles = data['subtitles']
    for key, low, high in (('max_chars_per_line', 8, 200), ('max_lines', 1, 6)):
        value = subtitles.get(key, 42 if key == 'max_chars_per_line' else 2)
        if type(value) is not int or not low <= value <= high:
            raise ValidationError(f'config.subtitles.{key} must be an integer {low}..{high}')
    _number(subtitles.get('safe_area_bottom_percent', 8), 'config.subtitles.safe_area_bottom_percent', 0, 40)
    if type(data['planning'].get('use_ai', True)) is not bool:
        raise ValidationError('config.planning.use_ai must be true or false')
    retry = data['retry']
    attempts = retry.get('render_max_attempts', 1)
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValidationError('config.retry.render_max_attempts must be 1..5')
    backoff = retry.get('backoff_seconds', [])
    if not isinstance(backoff, list):
        raise ValidationError('config.retry.backoff_seconds must be a list of seconds')
    for value in backoff:
        _number(value, 'config.retry.backoff_seconds[]', 0, 600)


SEQUENCE_PATTERNS = (
    'frame_%04d.png', 'frame_%04d.jpg', 'frame_%04d.jpeg',
    'frame_%05d.png', 'frame_%05d.jpg', 'frame_%05d.jpeg',
)


def validate_sequence(seq: Any, sid: str) -> dict[str, Any]:
    seq = ensure_object(seq, f'scene {sid}.sequence')
    if set(seq) != {'frames_dir', 'frame_pattern', 'frame_count', 'fps'}:
        raise ValidationError(f'scene {sid}: sequence requires frames_dir, frame_pattern, frame_count, fps')
    directory = check_relpath(seq['frames_dir'], f'scene {sid}.sequence.frames_dir')
    if not directory.startswith('assets/sequences/'):
        raise ValidationError('Sequence directory must be within assets/sequences/')
    if require_string(seq['frame_pattern'], 'sequence.frame_pattern') not in SEQUENCE_PATTERNS:
        raise ValidationError('sequence.frame_pattern must be frame_%04d or frame_%05d with png/jpg/jpeg')
    if type(seq['frame_count']) is not int or not 1 <= seq['frame_count'] <= 18000:
        raise ValidationError('sequence.frame_count must be 1..18000')
    try:
        seq_fps = Fraction(require_string(seq['fps'], 'sequence.fps'))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValidationError('Invalid sequence fps') from exc
    if seq_fps <= 0 or seq_fps > 120:
        raise ValidationError('Sequence fps must be >0 and <=120')
    return seq


def validate_storyboard(raw: Any, *, project_id: str | None = None) -> dict[str, Any]:
    data = ensure_object(raw, 'storyboard')
    if data.get('schema_version') != SCHEMA_VERSION:
        raise ValidationError(f'Unsupported storyboard schema_version: {data.get("schema_version")!r}')
    pid = require_string(data.get('project_id'), 'storyboard.project_id')
    if not PROJECT_ID.fullmatch(pid):
        raise ValidationError('Invalid storyboard.project_id')
    if project_id is not None and pid != project_id:
        raise ValidationError('storyboard.project_id does not match project folder')
    require_string(data.get('title'), 'storyboard.title')
    voice_mode = data.get('voice_mode')
    if voice_mode != 'per_scene':
        raise ValidationError('Phase 1.1 supports voice_mode=per_scene only; single_track begins in Phase 3')
    scenes = data.get('scenes')
    if not isinstance(scenes, list):
        raise ValidationError('storyboard.scenes must be an array')
    ids: set[str] = set()
    for index, item in enumerate(scenes, 1):
        scene = ensure_object(item, f'scenes[{index-1}]')
        sid = require_string(scene.get('scene_id'), f'scenes[{index-1}].scene_id')
        if not SCENE_ID.fullmatch(sid) or sid in ids:
            raise ValidationError(f'Invalid or duplicate scene_id: {sid}')
        ids.add(sid)
        if type(scene.get('order')) is not int or scene['order'] != index:
            raise ValidationError(f'scene {sid}: order must equal its 1-based array position ({index})')
        kind = scene.get('type', 'image')
        if kind not in ('image', 'image_sequence', 'video_clip'):
            raise ValidationError(f'scene {sid}: unsupported type {kind!r}')
        if 'overlay' in scene:
            # Optional static text/graphics layer composited after motion (Phase 2.5.1.7).
            if kind != 'image':
                raise ValidationError(f'scene {sid}: overlay is supported on image scenes only')
            overlay = check_relpath(scene['overlay'], f'scene {sid}.overlay')
            if not overlay.startswith('assets/overlays/') or not overlay.lower().endswith('.png'):
                raise ValidationError(f'scene {sid}: overlay must be a PNG inside assets/overlays/')
        if kind == 'image':
            require_string(scene.get('narration'), f'scene {sid}.narration')
            check_relpath(scene.get('image'), f'scene {sid}.image')
            check_relpath(scene.get('audio'), f'scene {sid}.audio')
        elif kind == 'video_clip':
            if not isinstance(scene.get('narration', ''), str):
                raise ValidationError(f'scene {sid}: narration must be string')
            video_path = check_relpath(scene.get('video'), f'scene {sid}.video')
            if not video_path.startswith('assets/videos/') or Path(video_path).suffix.lower() not in ('.mp4','.mov','.mkv'):
                raise ValidationError(f'scene {sid}: video must be mp4/mov/mkv inside assets/videos/')
            source_audio = scene.get('source_audio', {})
            if not isinstance(source_audio, dict) or any(k not in ('enabled', 'volume') for k in source_audio):
                raise ValidationError(f'scene {sid}: source_audio must contain only enabled and volume')
            if 'enabled' in source_audio and type(source_audio['enabled']) is not bool:
                raise ValidationError(f'scene {sid}: source_audio.enabled must be boolean')
            if 'volume' in source_audio:
                value = source_audio['volume']
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2:
                    raise ValidationError(f'scene {sid}: source_audio.volume must be finite 0..2')
            if 'image' in scene or 'sequence' in scene:
                raise ValidationError(f'scene {sid}: video_clip cannot also specify image or sequence')
            if scene.get('audio') is not None:
                audio_path = check_relpath(scene['audio'], f'scene {sid}.audio')
                if not audio_path.startswith('assets/audio/'):
                    raise ValidationError(f'scene {sid}: audio must be under assets/audio/')
            elif scene.get('narration', '').strip():
                raise ValidationError(f'scene {sid}: narration requires audio')
        else:
            if not isinstance(scene.get('narration', ''), str):
                raise ValidationError(f'scene {sid}: narration must be string')
            if 'image' in scene:
                raise ValidationError(f'scene {sid}: image_sequence uses sequence, not image')
            if scene.get('audio') is not None:
                check_relpath(scene['audio'], f'scene {sid}.audio')
            elif scene.get('narration', '').strip():
                raise ValidationError(f'scene {sid}: narration without audio is not supported')
            validate_sequence(scene.get('sequence'), sid)
    for i, track in enumerate(data.get('audio_tracks', [])):
        if not isinstance(track, dict) or set(track) != {'file','start_seconds','volume'}:
            raise ValidationError('audio_tracks entries require file, start_seconds, volume')
        f = check_relpath(track['file'], f'audio_tracks[{i}].file')
        if not f.startswith('assets/audio/'):
            raise ValidationError('audio_tracks files must be stored under assets/audio/')
        for field in ('start_seconds','volume'):
            v = track[field]
            if isinstance(v, bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
                raise ValidationError(f'audio_tracks[{i}].{field} must be finite number')
        if track['start_seconds'] < 0 or not 0 <= track['volume'] <= 2:
            raise ValidationError('audio_tracks start_seconds >=0 and volume 0..2')
    if 'subtitle_file' in data:
        f = check_relpath(data['subtitle_file'], 'subtitle_file')
        if not f.startswith('assets/subtitles/') or not f.lower().endswith('.srt'):
            raise ValidationError('subtitle_file must be SRT within assets/subtitles/')
    for i, cue in enumerate(data.get('subtitles', [])):
        if not isinstance(cue,dict) or set(cue) != {'start_seconds','end_seconds','text'}:
            raise ValidationError(f'subtitles[{i}]: requires start_seconds,end_seconds,text')
        if not isinstance(cue['text'],str) or not cue['text'].strip():
            raise ValidationError(f'subtitles[{i}].text must not be blank')
        start, end = cue['start_seconds'], cue['end_seconds']
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (start,end)) or start < 0 or end <= start:
            raise ValidationError(f'subtitles[{i}] invalid start/end')
    if data.get('subtitles') and data.get('subtitle_file'):
        raise ValidationError('Specify subtitles or subtitle_file, not both')
    return data


def atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.write-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open('r', encoding='utf-8') as f:
            return ensure_object(json.load(f), path.name)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f'Cannot read JSON {path}: {exc}') from exc


@dataclass
class Project:
    folder: Path
    config: dict[str, Any]
    storyboard: dict[str, Any]
    state: dict[str, Any]

    @property
    def project_id(self) -> str:
        return self.storyboard['project_id']


class ProjectManager:
    def __init__(self, projects_dir: Path, default_config: Path):
        self.projects_dir = Path(projects_dir).expanduser().resolve()
        self.default_config = Path(default_config).expanduser().resolve()

    def _folder(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise ValidationError('project_id must contain 1–64 ASCII letters, digits, _ or -')
        return self.projects_dir / project_id

    def create_project(self, project_id: str, title: str, *, config_overrides: dict[str, Any] | None = None) -> Project:
        title = require_string(title, 'title')
        target = self._folder(project_id)
        if target.exists():
            raise ValidationError(f'Project already exists: {project_id}')
        default = read_json(self.default_config)
        config = copy.deepcopy(default)
        if config_overrides is not None:
            self._deep_merge(config, ensure_object(config_overrides, 'config_overrides'))
        validate_config(config)
        storyboard = {'schema_version':SCHEMA_VERSION, 'project_id':project_id,
                      'title':title, 'voice_mode':'per_scene', 'scenes':[]}
        validate_storyboard(storyboard, project_id=project_id)
        state = {'schema_version':SCHEMA_VERSION, 'project_id':project_id, 'status':'CREATED',
                 'progress_percent':0, 'current_step':None, 'last_error':None}
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        # Stage the complete project in the same filesystem before publishing it.
        staging = self.projects_dir / ('.creating-' + uuid.uuid4().hex)
        try:
            staging.mkdir()
            for folder in ('assets/images','assets/audio','assets/sequences','assets/videos','assets/subtitles','assets/music','assets/fonts','assets/overlays',
                           'subtitles','work/scenes','work/transitions','work/audio','output','reports'):
                (staging / folder).mkdir(parents=True)
            atomic_write_json(staging / 'config.json', config)
            atomic_write_json(staging / 'storyboard.json', storyboard)
            atomic_write_json(staging / 'job_state.json', state)
            if target.exists():
                raise ValidationError(f'Project already exists: {project_id}')
            staging.rename(target)
        finally:
            if staging.exists():
                import shutil
                shutil.rmtree(staging)
        return self.load_project(project_id)

    @staticmethod
    def _deep_merge(destination: dict[str, Any], overrides: dict[str, Any]) -> None:
        for key, value in overrides.items():
            if key not in destination:
                raise ValidationError(f'Unknown config override: {key}')
            if isinstance(destination[key], dict):
                if not isinstance(value, dict):
                    raise ValidationError(f'Config override {key} must be an object')
                ProjectManager._deep_merge(destination[key], value)
            else:
                destination[key] = value

    def load_project(self, project_id: str) -> Project:
        folder = self._folder(project_id)
        if not folder.is_dir() or folder.is_symlink():
            raise ValidationError(f'Project not found or unsafe folder: {project_id}')
        config = validate_config(read_json(folder / 'config.json'))
        storyboard = validate_storyboard(read_json(folder / 'storyboard.json'), project_id=project_id)
        state = read_json(folder / 'job_state.json')
        if state.get('schema_version') != SCHEMA_VERSION or state.get('project_id') != project_id:
            raise ValidationError('Invalid job_state schema_version or project_id')
        if state.get('status') != 'CREATED':
            # Other lifecycle states will be introduced with the orchestrator in Phase 1.7.
            raise ValidationError(f'Unsupported Phase 1.1 job status: {state.get("status")!r}')
        for scene in storyboard['scenes']:
            if scene.get('type', 'image') == 'image':
                ensure_inside(folder, scene['image'])
            elif scene.get('type') == 'video_clip':
                ensure_inside(folder, scene['video'])
            else:
                ensure_inside(folder, scene['sequence']['frames_dir'])
            if scene.get('audio'):
                ensure_inside(folder, scene['audio'])
        return Project(folder, config, storyboard, state)

    def save_project(self, project_id: str, *, config: dict[str, Any] | None = None,
                     storyboard: dict[str, Any] | None = None) -> Project:
        project = self.load_project(project_id)
        new_config = copy.deepcopy(config if config is not None else project.config)
        new_storyboard = copy.deepcopy(storyboard if storyboard is not None else project.storyboard)
        validate_config(new_config)
        validate_storyboard(new_storyboard, project_id=project_id)
        for scene in new_storyboard['scenes']:
            if scene.get('type', 'image') == 'image':
                ensure_inside(project.folder, scene['image'])
            elif scene.get('type') == 'video_clip':
                ensure_inside(project.folder, scene['video'])
            else:
                ensure_inside(project.folder, scene['sequence']['frames_dir'])
            if scene.get('audio'):
                ensure_inside(project.folder, scene['audio'])
        # Each JSON is atomic; paired config+storyboard is NOT a cross-file transaction.
        if config is not None:
            atomic_write_json(project.folder / 'config.json', new_config)
        if storyboard is not None:
            atomic_write_json(project.folder / 'storyboard.json', new_storyboard)
        return self.load_project(project_id)

    def get_project_status(self, project_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.load_project(project_id).state)

    def list_projects(self) -> list[str]:
        if not self.projects_dir.exists():
            return []
        return sorted(p.name for p in self.projects_dir.iterdir()
                      if p.is_dir() and not p.is_symlink() and PROJECT_ID.fullmatch(p.name)
                      and (p / 'storyboard.json').is_file())
