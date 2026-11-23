"""Phase 2.5.1 — headless CLI services sharing the GUI's provider, asset and limit logic.

Importing this module never imports FastAPI or requires a web server.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from .project import ValidationError
from .agent.asset_workflow import (make_prompt_pack, generate_openai_asset, save_generated_asset,
                                   output_aspect_ratio, openai_image_size, voice_instructions)
from .api.provider_settings import (
    load_registry, load_choices, save_choices, add_provider, add_model,
    provider_info, get_key, save_key, public_settings,
)
from .api.voice_settings import load as load_voice, save as save_voice, validate_voice
from .api.gemini_provider import generate_asset as gemini_asset
from .api.limits import usage, reserve
from .api.workflow import bind_generated


def resolve_prompt(scene: dict, kind: str) -> str:
    """Avoid passing media prompts as spoken narration (regression from 2.4.10)."""
    prompt = (scene.get('audio_prompt') if kind == 'audio' else
              (scene.get('image_prompt') or scene.get('video_prompt')))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValidationError(f'No {kind} prompt/narration for scene {scene.get("scene_id")}')
    if kind == 'audio' and prompt == (scene.get('image_prompt') or scene.get('video_prompt')):
        raise ValidationError('Audio narration unexpectedly matches media prompt')
    return prompt.strip()


def planned_scenes(project, kind: str, ids: list[str] | None):
    if kind not in ('image', 'audio'):
        raise ValidationError('Only image and audio generation are supported; video prompts remain manual')
    pack = make_prompt_pack(project)
    mapping = {item['scene_id']: item for item in pack['scenes']}
    chosen = ids if ids is not None else [s['scene_id'] for s in pack['scenes']
             if kind == 'audio' and s.get('audio_prompt', '').strip() or
             kind == 'image' and s.get('image_prompt', '').strip()]
    if not chosen or len(chosen) != len(set(chosen)):
        raise ValidationError('Select one or more distinct scene IDs')
    result = []
    for sid in chosen:
        if sid not in mapping:
            raise ValidationError(f'Scene not found: {sid}')
        result.append((sid, resolve_prompt(mapping[sid], kind)))
    return result


def confirm_or_fail(description: str, yes: bool, *, input_fn: Callable[[str], str] = input):
    if yes:
        return
    if not sys.stdin.isatty():
        raise ValidationError('Non-interactive paid generation requires explicit --yes')
    response = input_fn(description + '\nType YES to authorize billable requests: ')
    if response != 'YES':
        raise ValidationError('Paid generation cancelled; no quota consumed')


def generate(project, projects_dir: Path, *, kind: str, scene_ids: list[str] | None,
             voice: str | None = None, yes: bool = False, bind: bool = False,
             output: Callable[[str], None] = print):
    prompts = planned_scenes(project, kind, scene_ids)
    choice = load_choices(projects_dir)[kind]
    provider, model = choice['provider'], choice['model']
    info = provider_info(projects_dir, provider)
    key = get_key(provider, projects_dir)
    if not key:
        raise ValidationError(f'Configure API key for {provider} before generation; no quota consumed')
    selected_voice = None
    if kind == 'audio':
        selected_voice = (validate_voice(voice, provider, info['adapter']) if voice else
                          load_voice(project, provider, info['adapter']))
    available = usage(project)
    remaining = available['remaining'][kind]
    if len(prompts) > remaining:
        raise ValidationError(f'Only {remaining} {kind} attempts remaining; {len(prompts)} selected. '
                              'Adjust project API limits in GUI before retrying')
    output(f'Provider: {provider} | Model: {model} | Task: {kind} | Scenes: {len(prompts)}'
           + (f' | Voice: {selected_voice}' if selected_voice else ''))
    output(f'Local attempts remaining today: {remaining} (not provider account credit)')
    confirm_or_fail(f'Generate {len(prompts)} paid {kind} asset(s)?', yes)
    if selected_voice is not None and voice:
        save_voice(project, provider, info['adapter'], selected_voice)
    results = []
    ratio = output_aspect_ratio(project)
    directions = voice_instructions(project) if kind == 'audio' else {}
    for index, (sid, prompt) in enumerate(prompts, 1):
        # Charge per attempted call so a partially failed batch does not reserve unused calls.
        # Ensure no quota is consumed until validation, key check and confirmation complete.
        try:
            reserve(project, kind)
        except ValueError as exc:
            # Cap reached mid-batch (e.g. GUI used attempts meanwhile): stop cleanly, no API call.
            raise ValidationError(str(exc)) from exc
        output(f'[{index}/{len(prompts)}] Scene {sid}: calling {provider}...')
        try:
            if info['adapter'] in ('openai', 'openai_compatible'):
                data, ext, returned_model = generate_openai_asset(
                    kind, prompt, model=model, voice=selected_voice or 'alloy',
                    api_key=key, base_url=info['base_url'],
                    compatible=info['adapter'] == 'openai_compatible',
                    size=openai_image_size(ratio), instructions=directions.get(sid))
            elif info['adapter'] == 'gemini':
                data, ext, returned_model = gemini_asset(
                    kind, prompt, model, voice=selected_voice or 'Kore', projects_dir=projects_dir,
                    aspect_ratio=ratio if kind == 'image' else None)
            else:
                raise ValidationError(f'Unsupported provider adapter: {info["adapter"]}')
            saved = save_generated_asset(project, sid, kind, data, ext, returned_model,
                                         source_prompt=prompt, provider=provider)
            output(f'  OK: {saved["file"]}')
            results.append({'scene_id': sid, 'status': 'succeeded', 'file': saved['file']})
        except Exception as exc:
            # Do not print raw provider responses or credentials.
            message = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
            output(f'  FAILED: {message}')
            results.append({'scene_id': sid, 'status': 'failed', 'error': message})
    report = {'project_id': project.project_id, 'provider': provider, 'model': model,
              'kind': kind, 'voice': selected_voice, 'results': results,
              'succeeded': sum(s['status'] == 'succeeded' for s in results),
              'failed': sum(s['status'] == 'failed' for s in results)}
    if bind:
        try:
            report['binding'] = bind_generated(project)
        except (ValidationError, OSError, ValueError) as exc:
            report['binding_warning'] = str(exc)
    return report


def provider_command(args, projects_dir: Path):
    if args.provider_action == 'list':
        return public_settings(projects_dir)
    if args.provider_action == 'models':
        catalog = load_registry(projects_dir)
        if args.provider_id not in catalog['providers']:
            raise ValidationError('Unknown provider')
        return {'provider': args.provider_id, 'models': {
            task: models.get(args.provider_id, []) for task, models in catalog['models'].items()}}
    if args.provider_action == 'add':
        return add_provider(projects_dir, provider_id=args.provider_id, label=args.label,
                            base_url=args.base_url)
    if args.provider_action == 'add-model':
        return add_model(projects_dir, provider_id=args.provider_id, task=args.task, model=args.model)
    if args.provider_action == 'set':
        return save_choices(projects_dir, {args.task: {'provider': args.provider_id,
                                                       'model': args.model}})
    if args.provider_action == 'key-status':
        return {'provider': args.provider_id, 'configured': bool(get_key(args.provider_id, projects_dir))}
    if args.provider_action == 'key-set':
        if not sys.stdin.isatty() and not args.stdin:
            raise ValidationError('Use --stdin in noninteractive mode; never pass secrets as command arguments')
        if args.stdin:
            key = sys.stdin.readline().rstrip('\r\n')
        else:
            from getpass import getpass
            key = getpass('API key (hidden): ')
        save_key(args.provider_id, key, projects_dir)
        return {'provider': args.provider_id, 'configured': True}
    raise ValidationError('Unknown provider action')


def generate_text_with_settings(projects_dir: Path, task: str, content):
    """Use the same configured provider/model for text tasks as the Web GUI."""
    if task not in ('script', 'scene_plan'):
        raise ValidationError('Unsupported text task')
    choice = load_choices(projects_dir)[task]
    provider, model = choice['provider'], choice['model']
    info = provider_info(projects_dir, provider)
    key = get_key(provider, projects_dir)
    if not key:
        raise ValidationError(f'Configure API key for {provider} before generation')
    if info['adapter'] in ('openai', 'openai_compatible'):
        if task == 'script':
            from .agent.script_generator import openai_generate
            result, used = openai_generate(content, model=model, api_key=key, base_url=info['base_url'])
        else:
            from .agent.scene_planner import generate_openai_scene_plan
            result, used = generate_openai_scene_plan(content, model=model, api_key=key, base_url=info['base_url'])
        used['provider'] = provider
    elif info['adapter'] == 'gemini':
        from .api.gemini_provider import generate_text as gemini_text
        source = content if task == 'script' else json.dumps(content, ensure_ascii=False)
        result, used = gemini_text(task, source, model, projects_dir=projects_dir)
    else:
        raise ValidationError(f'Unsupported provider adapter: {info["adapter"]}')
    return result, used, provider
