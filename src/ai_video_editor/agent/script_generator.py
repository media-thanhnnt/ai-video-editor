"""Phase 2.2: reviewable requirement and script drafts, no media generation.

The OpenAI endpoint is called only on explicit request. Offline import is supported
for scripts prepared in existing chatbot subscriptions. Secrets are never persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from ..project import Project, ValidationError, atomic_write_json, read_json
from .manifest import validate_brief

DRAFT_NAME = 'agent_script_draft.json'
BRIEF_NAME = 'agent_brief_generated.json'
ALLOWED_STYLES = {'documentary', 'calm', 'energetic'}
ALLOWED_MEDIA = {'image', 'video_clip'}


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _schema() -> dict[str, Any]:
    scene = {'type': 'object', 'additionalProperties': False,
             'properties': {
                'scene_id': {'type': 'string'},
                'type': {'type': 'string', 'enum': ['image', 'video_clip']},
                'narration': {'type': 'string'},
                'prompt': {'type': 'string'},
                'estimated_duration_seconds': {'type': 'number'},
             }, 'required': ['scene_id', 'type', 'narration', 'prompt', 'estimated_duration_seconds']}
    return {'type': 'object', 'additionalProperties': False,
            'properties': {
                'title': {'type': 'string'},
                'language': {'type': 'string'},
                'style': {'type': 'string', 'enum': sorted(ALLOWED_STYLES)},
                'target_duration_seconds': {'type': 'number'},
                'summary': {'type': 'string'},
                'script': {'type': 'string'},
                'scenes': {'type': 'array', 'items': scene},
            }, 'required': ['title', 'language', 'style', 'target_duration_seconds',
                             'summary', 'script', 'scenes']}


def validate_script(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError('Script draft must be an object')
    for field in ('title', 'language', 'summary', 'script'):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValidationError(f'Script draft requires nonempty {field}')
    if data.get('style') not in ALLOWED_STYLES:
        raise ValidationError('Invalid script style')
    duration = data.get('target_duration_seconds')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 5 <= duration <= 7200:
        raise ValidationError('target_duration_seconds must be between 5 and 7200')
    scenes = data.get('scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 120:
        raise ValidationError('Script must contain 1..120 scenes')
    seen = set()
    total = 0.0
    for n, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValidationError('Script scene must be object')
        sid = scene.get('scene_id')
        if not isinstance(sid, str) or not sid.isascii() or not sid.replace('_','').isalnum() or sid in seen:
            raise ValidationError(f'Invalid/duplicate scene id at scene {n}')
        seen.add(sid)
        if scene.get('type') not in ALLOWED_MEDIA:
            raise ValidationError(f'Scene {sid}: type must be image or video_clip')
        for field in ('narration', 'prompt'):
            if not isinstance(scene.get(field), str):
                raise ValidationError(f'Scene {sid}: {field} must be a string')
        if not scene['prompt'].strip() or (scene['type'] == 'image' and not scene['narration'].strip()):
            raise ValidationError(f'Scene {sid}: prompt and image narration required')
        seconds = scene.get('estimated_duration_seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 600:
            raise ValidationError(f'Scene {sid}: invalid estimated duration')
        total += seconds
    # Timing is provisional until real TTS/media are imported; warn instead of asserting exact equality.
    return data


def _extract_response_text(payload: dict[str, Any]) -> str:
    if payload.get('status') == 'incomplete':
        raise ValidationError('OpenAI response incomplete; increase output token budget or shorten request')
    chunks = []
    for item in payload.get('output', []):
        if item.get('type') == 'message':
            for part in item.get('content', []):
                if part.get('type') == 'output_text':
                    chunks.append(part.get('text', ''))
                if part.get('type') == 'refusal':
                    raise ValidationError('OpenAI declined the script request')
    if not chunks:
        raise ValidationError('OpenAI returned no script text')
    return ''.join(chunks)


def openai_generate(brief: str, *, model: str = 'gpt-4.1-mini',
                    api_key: str | None = None, timeout: int = 120,
                    opener: Callable[..., Any] | None = None,
                    base_url: str = 'https://api.openai.com/v1') -> tuple[dict[str, Any], dict[str, Any]]:
    key = (api_key if api_key is not None else (os.environ.get('OPENAI_API_KEY') if base_url.rstrip('/') == 'https://api.openai.com/v1' else None))
    if not key:
        raise ValidationError('OPENAI_API_KEY missing; use --import-json for manual chatbot output')
    if not isinstance(model, str) or not model.strip():
        raise ValidationError('Model name required')
    body = {
        'model': model,
        'store': False,
        'instructions': (
            'You are a video pre-production script writer. Return ONLY a valid structured JSON. '
            'Write factual claims carefully; avoid fabricated citations. '
            'Respect requested language, duration and scene count where practicable. '
            'Make natural narration for every image scene. Video scenes may have empty narration. '
            'Scene ids must be 001, 002, ... Prompt fields are prompts for manual asset creation. '
            'Use only image and video_clip types. The full script is narration prose, not metadata. '
            'Durations are estimates, not synchronized timestamps. No real files are generated.'
        ),
        'input': brief,
        'text': {'format': {'type': 'json_schema', 'name': 'video_script',
                            'strict': True, 'schema': _schema()}},
    }
    req = request.Request(base_url.rstrip('/') + '/responses',
                          data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          headers={'Authorization': f'Bearer {key}',
                                   'Content-Type': 'application/json'}, method='POST')
    try:
        with (opener or request.urlopen)(req, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        # Do not log the request object, which includes authorization header.
        raise ValidationError(f'OpenAI HTTP {exc.code}; check model, key, quota and API settings') from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ValidationError(f'OpenAI connection failed: {type(exc).__name__}') from exc
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('response payload must be an object')
        result = json.loads(_extract_response_text(payload))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValidationError('Invalid structured JSON response from OpenAI') from exc
    validate_script(result)
    usage = payload.get('usage') or {}
    return result, {'provider': 'openai', 'model': model,
                    'input_tokens': int(usage.get('input_tokens') or 0),
                    'output_tokens': int(usage.get('output_tokens') or 0),
                    'total_tokens': int(usage.get('total_tokens') or 0),
                    'estimated_cost_usd': None}


def create_script_draft(project: Project, result: dict[str, Any], *, source: str,
                        brief: str = '', usage: dict[str, Any] | None = None,
                        replace: bool = False) -> dict[str, Any]:
    validate_script(result)
    path = project.folder / DRAFT_NAME
    if path.exists() and not replace:
        raise ValidationError('Agent script draft exists; use --replace to overwrite')
    draft = {'schema_version': '2.2', 'project_id': project.project_id,
             'status': 'AWAITING_SCRIPT_APPROVAL', 'source': source,
             'requirement': brief, 'content': result, 'approved_hash': None,
             'usage': usage or {'provider': 'manual', 'input_tokens': 0,
                                 'output_tokens': 0, 'total_tokens': 0,
                                 'estimated_cost_usd': 0}}
    atomic_write_json(path, draft)
    return draft


def load_script_draft(project: Project) -> dict[str, Any]:
    draft = read_json(project.folder / DRAFT_NAME)
    if draft.get('schema_version') != '2.2' or draft.get('project_id') != project.project_id:
        raise ValidationError('Invalid Phase 2.2 draft identity')
    validate_script(draft.get('content'))
    return draft


def approve_script(project: Project) -> dict[str, Any]:
    draft = load_script_draft(project)
    draft['approved_hash'] = digest(draft['content'])
    draft['status'] = 'SCRIPT_APPROVED'
    atomic_write_json(project.folder / DRAFT_NAME, draft)
    return {'project_id': project.project_id, 'status': draft['status'],
            'scenes': len(draft['content']['scenes'])}


def export_agent_brief(project: Project, *, replace: bool = False) -> dict[str, Any]:
    draft = load_script_draft(project)
    if draft.get('status') != 'SCRIPT_APPROVED' or draft.get('approved_hash') != digest(draft['content']):
        raise ValidationError('Script not approved or changed since approval')
    brief = {'title': draft['content']['title'],
             'scenes': [{k: s[k] for k in ('scene_id', 'type', 'narration', 'prompt')}
                        for s in draft['content']['scenes']]}
    validate_brief(brief)
    path = project.folder / BRIEF_NAME
    if path.exists() and not replace:
        raise ValidationError('Agent brief already exists; use --replace to overwrite')
    atomic_write_json(path, brief)
    return {'project_id': project.project_id, 'file': str(path),
            'status': 'BRIEF_READY', 'scenes': len(brief['scenes'])}
