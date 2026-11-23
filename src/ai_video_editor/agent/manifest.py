"""Phase 2.1: safe, offline, human-in-the-loop asset planning orchestration.

This module does NOT call an LLM or a media API.  A future planning provider can
produce the validated brief; the human supplies and approves its media inputs.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from ..assets import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS
from ..project import (Project, ValidationError, validate_sequence, atomic_write_json, check_relpath,
                      ensure_inside, read_json, validate_storyboard)

SCHEMA = '2.1'
MANIFEST = 'agent_manifest.json'
ALLOWED = {'image', 'image_sequence', 'video_clip'}


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(brief, dict):
        raise ValidationError('Agent brief must be a JSON object')
    if not isinstance(brief.get('title'), str) or not brief['title'].strip():
        raise ValidationError('brief.title is required')
    scenes = brief.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        raise ValidationError('brief.scenes must be a nonempty array')
    ids = set()
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValidationError('Each brief scene must be an object')
        sid = scene.get('scene_id', f'{index:03d}')
        if not isinstance(sid, str) or not sid.isascii() or not sid.replace('_','').isalnum() or sid in ids:
            raise ValidationError(f'Invalid/duplicate brief scene_id: {sid!r}')
        ids.add(sid)
        kind = scene.get('type')
        if kind not in ALLOWED:
            raise ValidationError(f'Scene {sid}: type must be image, image_sequence or video_clip')
        if not isinstance(scene.get('prompt', ''), str) or not isinstance(scene.get('narration', ''), str):
            raise ValidationError(f'Scene {sid}: prompt and narration must be strings')
        if kind == 'image' and not scene.get('narration', '').strip():
            raise ValidationError(f'Scene {sid}: Phase 1 Core requires narration and audio for image scenes')
        if kind == 'image_sequence':
            validate_sequence(scene.get('sequence'), sid)
    return brief


def _scene_request(raw: dict[str, Any], index: int) -> dict[str, Any]:
    return {'scene_id': raw.get('scene_id', f'{index:03d}'),
            'type': raw['type'], 'narration': raw.get('narration', ''),
            'prompt': raw.get('prompt', ''), 'media_file': None, 'voice_file': None,
            **({'sequence': copy.deepcopy(raw['sequence'])} if raw['type']=='image_sequence' else {})}


def create_manifest(project: Project, brief: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    validate_brief(brief)
    path = project.folder / MANIFEST
    if path.exists() and not replace:
        raise ValidationError('Agent manifest already exists; use --replace to create a new draft')
    data = {'schema_version': SCHEMA, 'project_id': project.project_id,
            'title': brief['title'], 'status':'WAITING_FOR_ASSETS', 'approved_hash': None,
            'scenes': [_scene_request(s, i) for i,s in enumerate(brief['scenes'],1)]}
    atomic_write_json(path, data)
    return data


def load_manifest(project: Project) -> dict[str, Any]:
    data = read_json(project.folder / MANIFEST)
    if data.get('schema_version') != SCHEMA or data.get('project_id') != project.project_id:
        raise ValidationError('Invalid agent manifest project/schema')
    ids = set()
    for scene in data.get('scenes', []):
        if not isinstance(scene, dict) or scene.get('type') not in ALLOWED:
            raise ValidationError('Malformed agent manifest scene')
        if scene.get('scene_id') in ids:
            raise ValidationError('Duplicate agent manifest scene')
        ids.add(scene['scene_id'])
        for field in ('media_file','voice_file'):
            if scene.get(field) is not None:
                check_relpath(scene[field], field)
    return data


def _requires_voice(scene: dict[str, Any]) -> bool:
    return bool(scene.get('narration', '').strip()) or scene['type'] == 'image'


def missing_assets(project: Project, data: dict[str, Any]) -> list[str]:
    missing = []
    for scene in data['scenes']:
        sid, kind = scene['scene_id'], scene['type']
        if kind == 'image_sequence':
            seq = scene['sequence']
            # The Core performs actual sequence frame count validation later.
            path = ensure_inside(project.folder, seq['frames_dir'])
            if not path.is_dir():
                missing.append(f'{sid}:sequence_directory')
        else:
            media = scene.get('media_file')
            if not media or not ensure_inside(project.folder, media).is_file():
                missing.append(f'{sid}:media')
        if _requires_voice(scene):
            voice = scene.get('voice_file')
            if not voice or not ensure_inside(project.folder, voice).is_file():
                missing.append(f'{sid}:voice')
    return missing


def _approval_material(data: dict[str, Any]) -> dict[str, Any]:
    # Bindings may arrive after review, but the creative plan cannot silently change.
    return {'title':data['title'], 'scenes':[
        {key:scene.get(key) for key in ('scene_id','type','narration','prompt','sequence')}
        for scene in data['scenes']]}


def bind_asset(project: Project, scene_id: str, kind: str, relative_file: str) -> dict[str, Any]:
    if kind not in ('media','voice'):
        raise ValidationError('Asset kind must be media or voice')
    data=load_manifest(project)
    scene=next((s for s in data['scenes'] if s['scene_id']==scene_id),None)
    if scene is None:
        raise ValidationError(f'Unknown scene {scene_id}')
    if kind=='media' and scene['type']=='image_sequence':
        raise ValidationError('Image sequences use sequence.frames_dir from the brief')
    relative=check_relpath(relative_file, 'file')
    root='assets/audio/' if kind=='voice' else ('assets/images/' if scene['type']=='image' else 'assets/videos/')
    if not relative.startswith(root):
        raise ValidationError(f'Asset must live under {root}')
    suffix=Path(relative).suffix.lower()
    allowed=AUDIO_EXTENSIONS if kind=='voice' else (IMAGE_EXTENSIONS if scene['type']=='image' else {'.mp4','.mov','.mkv'})
    if suffix not in allowed:
        raise ValidationError(f'Unsupported {kind} extension: {suffix}')
    if not ensure_inside(project.folder, relative).is_file():
        raise ValidationError(f'Asset not found: {relative}')
    field = 'voice_file' if kind=='voice' else 'media_file'
    if scene.get(field) == relative:
        return data  # Idempotent: no need to invalidate a compiled storyboard.
    scene[field] = relative
    data['status']='READY_FOR_REVIEW' if not missing_assets(project,data) else 'WAITING_FOR_ASSETS'
    atomic_write_json(project.folder / MANIFEST, data)
    return data


def approve_manifest(project: Project) -> dict[str, Any]:
    data=load_manifest(project)
    missing=missing_assets(project,data)
    if missing:
        raise ValidationError('Assets not ready: '+', '.join(missing))
    data['approved_hash']=_digest(_approval_material(data))
    data['status']='APPROVED'
    atomic_write_json(project.folder / MANIFEST,data)
    return data


def compile_storyboard(project: Project, *, replace_storyboard: bool = False) -> dict[str, Any]:
    data=load_manifest(project)
    if data.get('approved_hash') != _digest(_approval_material(data)):
        raise ValidationError('Agent plan not approved or changed since approval')
    missing=missing_assets(project,data)
    if missing:
        raise ValidationError('Assets missing: '+', '.join(missing))
    scenes=[]
    for index, s in enumerate(data['scenes'],1):
        kind=s['type']
        item={'scene_id':s['scene_id'],'order':index,'type':kind,'narration':s['narration']}
        if kind=='image':
            item['image']=s['media_file']; item['audio']=s['voice_file']
        elif kind=='image_sequence':
            item['sequence']=copy.deepcopy(s['sequence'])
            if s.get('voice_file'):
                item['audio']=s['voice_file']
        else:
            item['video']=s['media_file']
            if s.get('voice_file'):
                item['audio']=s['voice_file']
        scenes.append(item)
    storyboard={'schema_version':'1.1','project_id':project.project_id,
                'title':data['title'],'voice_mode':'per_scene','scenes':scenes}
    validate_storyboard(storyboard,project_id=project.project_id)
    output=project.folder/'storyboard.json'
    existing=read_json(output)
    if existing.get('scenes') and not replace_storyboard:
        raise ValidationError('Storyboard already contains scenes; use --replace-storyboard explicitly')
    # Keep an existing audio/subtitle configuration when compiling on an empty project.
    for key in ('audio_tracks','subtitles','subtitle_file'):
        if key in existing:
            storyboard[key]=existing[key]
    validate_storyboard(storyboard,project_id=project.project_id)
    atomic_write_json(output,storyboard)
    data['status']='COMPILED'
    atomic_write_json(project.folder / MANIFEST,data)
    return storyboard


