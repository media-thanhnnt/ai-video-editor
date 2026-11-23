"""Opt-in asset creation and manual prompt packs. No side effects during planning."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from urllib import request, error

from ..project import ValidationError, atomic_write_json, read_json
from .script_generator import DRAFT_NAME, load_script_draft, digest

MAX_ASSET_BYTES = 25 * 1024 * 1024

# Aspect ratios accepted by Gemini image models (generationConfig.imageConfig.aspectRatio).
GEMINI_ASPECT_RATIOS = ('1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9')


def output_aspect_ratio(project) -> str:
    """Closest supported image aspect ratio for the project's output frame (default 16:9).

    Without it Gemini returns square images that `fit_mode=cover` must crop heavily.
    """
    import math
    try:
        width = int(project.config['output']['width'])
        height = int(project.config['output']['height'])
        target = math.log(width / height)
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return '16:9'
    def distance(ratio):
        w, h = (int(v) for v in ratio.split(':'))
        return abs(math.log(w / h) - target)
    return min(GEMINI_ASPECT_RATIOS, key=distance)


def openai_image_size(aspect_ratio: str | None) -> str:
    """Map the project aspect ratio onto the three sizes every gpt-image model accepts."""
    try:
        w, h = (int(v) for v in (aspect_ratio or '16:9').split(':'))
        ratio = w / h
    except (ValueError, ZeroDivisionError):
        ratio = 16 / 9
    if ratio > 1.1:
        return '1536x1024'
    if ratio < 0.9:
        return '1024x1536'
    return '1024x1024'


def voice_instructions(project) -> dict:
    """Scene ID → voice direction text built for the prompt pack (sent to TTS models that support it)."""
    return {s['scene_id']: s.get('audio_instructions') for s in make_prompt_pack(project)['scenes']}


def make_prompt_pack(project):
    """Create read-only copyable prompts from SCRIPT, optionally enrich with approved scene plan."""
    script = load_script_draft(project)
    from .scene_planner import load_scene_plan
    planned = {}
    plan_file = project.folder / 'agent_scene_plan.json'
    if plan_file.is_file():
        try:
            plan = load_scene_plan(project)
            if plan['status'] == 'SCENE_PLAN_APPROVED':
                planned = {s['scene_id']: s for s in plan['content']['scenes']}
        except (ValidationError, OSError, ValueError):
            pass  # A stale/invalid plan must never taint source Script prompts.
    scenes = []
    for item in script['content']['scenes']:
        sid = item['scene_id']
        suggestion = planned.get(sid, {})
        kind = suggestion.get('media_type', item['type'])
        prompt = suggestion.get('asset_prompt_en', item['prompt'])
        narration = item['narration']
        scenes.append({
            'scene_id': sid, 'media_type': kind,
            'duration_seconds': suggestion.get('estimated_duration_seconds', item['estimated_duration_seconds']),
            'image_prompt': prompt if kind == 'image' else '',
            'video_prompt': prompt if kind == 'video_clip' else '',
            'audio_prompt': narration,
            'audio_instructions': 'Read naturally in ' + script['content']['language'] + ', matching the scene duration. Do not add extra spoken words.',
            'source': 'approved_scene_plan' if suggestion else 'script',
        })
    return {'project_id': project.project_id, 'title': script['content']['title'],
            'script_hash': digest(script['content']), 'scenes': scenes}


def render_prompt_markdown(pack):
    lines = [f"# {pack['title']} — Asset Prompts", '',
             'Copy prompts into your preferred image, video or voice tools. No API call is required.', '']
    for scene in pack['scenes']:
        lines.extend([f"## Scene {scene['scene_id']} — {scene['media_type']} ({scene['duration_seconds']}s)", ''])
        if scene['image_prompt']:
            lines.extend(['### Image prompt', scene['image_prompt'], ''])
        if scene['video_prompt']:
            lines.extend(['### Video prompt', scene['video_prompt'], ''])
        lines.extend(['### Audio / Voice narration', scene['audio_prompt'] or '(no narration)', '',
                      '### Voice directions', scene['audio_instructions'], ''])
    return '\n'.join(lines)


def _post_json(path, payload, *, key, timeout=180, base_url='https://api.openai.com/v1'):
    data = json.dumps(payload).encode('utf-8')
    req = request.Request(base_url.rstrip('/') + '/' + path, data=data,
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                          method='POST')
    try:
        with request.urlopen(req, timeout=timeout) as response:
            result = response.read(MAX_ASSET_BYTES * 2 + 1)
            if len(result) > MAX_ASSET_BYTES * 2:
                raise ValidationError('API response exceeded safe size limit')
            return result
    except error.HTTPError as exc:
        # Never include authentication headers or the request body in errors.
        raise ValidationError(f'OpenAI API returned HTTP {exc.code} for {path}') from exc
    except error.URLError as exc:
        raise ValidationError(f'Cannot reach OpenAI asset API: {exc.reason}') from exc


def generate_openai_asset(kind, prompt, *, model=None, voice='alloy', api_key=None,
                          base_url='https://api.openai.com/v1', compatible=False,
                          size='1536x1024', instructions=None):
    """Paid operation. Must be invoked only after explicit GUI confirmation."""
    key = (api_key if api_key is not None else (os.environ.get('OPENAI_API_KEY') if base_url.rstrip('/') == 'https://api.openai.com/v1' else None))
    if not key:
        raise ValidationError('OPENAI_API_KEY missing in UI server process')
    if not prompt or not prompt.strip() or len(prompt) > 8000:
        raise ValidationError('Asset prompt must be between 1 and 8000 characters')
    if kind == 'image':
        name = model or 'gpt-image-2'
        if not compatible and not name.startswith(('gpt-image-', 'dall-e-')):
            raise ValidationError('Unsupported image generation model')
        try:
            response = json.loads(_post_json('images/generations', {'model': name, 'prompt': prompt,
                                                    'size': size, 'n': 1}, key=key, base_url=base_url))
            if not isinstance(response, dict):
                raise ValueError('response not object')
            img = response['data'][0]['b64_json']
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise ValidationError('Image API returned invalid structured JSON') from exc
        if not isinstance(img, str):
            raise ValidationError('Image API did not return b64_json')
        try:
            content = base64.b64decode(img, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValidationError('Image API returned invalid image data') from exc
        if not content.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValidationError('Image API returned unexpected format; expected PNG')
        ext = '.png'
    elif kind == 'audio':
        name = model or 'gpt-4o-mini-tts'
        if not compatible and name not in ('gpt-4o-mini-tts', 'tts-1', 'tts-1-hd'):
            raise ValidationError('Unsupported speech model')
        if voice not in ('alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer'):
            raise ValidationError('Unsupported voice')
        body = {'model': name, 'voice': voice, 'input': prompt, 'response_format': 'mp3'}
        # Voice directions are supported by OpenAI's gpt-4o* TTS models, not tts-1/tts-1-hd.
        # Custom providers may reject unknown fields, so they never receive it.
        if (instructions and isinstance(instructions, str) and not compatible
                and not name.startswith('tts-')):
            body['instructions'] = instructions.strip()[:4000]
        content = _post_json('audio/speech', body, key=key, base_url=base_url)
        if not (content.startswith(b'ID3') or content[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')):
            raise ValidationError('Speech API returned unexpected format; expected MP3')
        ext = '.mp3'
    else:
        raise ValidationError('Video API is unavailable; use manual video prompt workflow')
    if not content or len(content) > MAX_ASSET_BYTES:
        raise ValidationError('Generated asset empty or exceeds 25 MB limit')
    return content, ext, name


def save_generated_asset(project, scene_id, kind, content, ext, model, *, source_prompt, provider='openai'):
    """Never clobber prior assets, write a unique local file with basic audit metadata."""
    if kind not in ('image', 'audio') or ext not in ('.png', '.jpg', '.mp3', '.wav'):
        raise ValidationError('Unsupported asset type')
    if not content or len(content) > MAX_ASSET_BYTES:
        raise ValidationError('Generated asset empty or exceeds 25 MB limit')
    folder = project.folder / 'assets' / ('images' if kind == 'image' else 'audio')
    folder.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(content).hexdigest()[:12]
    path = folder / f'generated_{kind}_{scene_id}_{fingerprint}{ext}'
    if not path.exists():
        with path.open('xb') as f:
            f.write(content)
    log_path = project.folder / 'generated_assets.json'
    log = read_json(log_path) if log_path.exists() else {'schema_version': '2.4.4', 'assets': []}
    rel = path.relative_to(project.folder).as_posix()
    if not any(it.get('path') == rel for it in log['assets']):
        log['assets'].append({'scene_id': scene_id, 'kind': kind, 'path': rel,
                              'provider': provider, 'model': model,
                              'prompt_sha256': hashlib.sha256(source_prompt.encode('utf-8')).hexdigest(),
                              'bytes': len(content)})
        atomic_write_json(log_path, log)
    return {'scene_id': scene_id, 'kind': kind, 'file': rel, 'bytes': len(content),
            'model': model, 'auto_bound': False}
