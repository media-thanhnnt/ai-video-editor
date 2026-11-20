"""Phase 1.4 deterministic, allow-listed, scene-specific editing plan.

No AI/network calls, media modification or storyboard mutation.
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from .project import Project, ValidationError, atomic_write_json


class PlanError(ValidationError):
    """An editing plan violates the contract or is stale."""


MOTIONS = frozenset(('static', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right'))
INTENSITIES = frozenset(('subtle', 'medium', 'strong'))
TRANSITIONS = frozenset(('none', 'crossfade', 'dissolve'))
LENGTHS = frozenset(('short', 'medium', 'long'))
STYLES = frozenset(('documentary', 'calm', 'energetic'))
PROMPT_VERSION = 'rules-v1.4.0'

# Each style defines deliberate, deterministic choices and conservative durations.
STYLE_PRESETS: dict[str, dict[str, Any]] = {
    'documentary': {
        'motions': ('zoom_in', 'pan_left', 'zoom_out', 'pan_right', 'static'),
        'intensities': ('subtle', 'medium'),
        'transitions': ('crossfade', 'dissolve', 'none'),
        'length': 'medium',
    },
    'calm': {
        'motions': ('static', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right'),
        'intensities': ('subtle',),
        'transitions': ('crossfade', 'dissolve'),
        'length': 'long',
    },
    'energetic': {
        'motions': ('pan_left', 'zoom_in', 'pan_right', 'zoom_out', 'static'),
        'intensities': ('medium', 'strong'),
        'transitions': ('none', 'crossfade'),
        'length': 'short',
    },
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def plan_cache_key(project: Project, style: str, seed: int) -> str:
    """Plan depends on visual scene structure and planning settings, never audio/subtitle overlays."""
    visual_scenes = []
    for scene in project.storyboard['scenes']:
        kind = scene.get('type', 'image')
        visual_scenes.append({k: scene[k] for k in ('scene_id','order','type','image','video','sequence')
                              if k in scene} | {'type': kind})
    visual_config = {'transitions': project.config['transitions']}
    payload = {
        'schema_version': '1.1', 'planner': PROMPT_VERSION,
        'visual_scenes': visual_scenes, 'visual_config': visual_config,
        'style': style, 'seed': seed,
    }
    return 'sha256:' + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _parameters(project: Project, style: str | None, seed: int | None) -> tuple[str, int]:
    style = project.config['planning']['style'] if style is None else style
    seed = project.config['planning']['seed'] if seed is None else seed
    if style not in STYLES:
        raise PlanError(f'Unknown planning style: {style!r}')
    if type(seed) is not int or not -(2**63) <= seed < 2**63:
        raise PlanError('seed must be a signed 64-bit integer')
    return style, seed


def validate_editing_plan(project: Project, plan: Any, *, require_current: bool = True) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != {'schema_version', 'plan_meta', 'style', 'scenes'}:
        raise PlanError('Plan must contain only schema_version, plan_meta, style, scenes')
    if plan['schema_version'] != '1.1' or plan['style'] not in STYLES:
        raise PlanError('Invalid plan schema_version/style')
    meta = plan['plan_meta']
    required_meta = {'source', 'provider', 'model', 'prompt_version', 'cache_key',
                     'revisions_used', 'approved_by_user', 'seed'}
    if not isinstance(meta, dict) or set(meta) != required_meta:
        raise PlanError('Invalid plan_meta structure')
    if (meta['source'] != 'rule_based' or meta['provider'] is not None or meta['model'] is not None
        or meta['prompt_version'] != PROMPT_VERSION or meta['revisions_used'] != 0
        or type(meta['approved_by_user']) is not bool or type(meta['seed']) is not int):
        raise PlanError('Invalid rule-based plan metadata')
    scenes = plan['scenes']
    expected_ids = [scene['scene_id'] for scene in project.storyboard['scenes']]
    if not isinstance(scenes, list) or [s.get('scene_id') if isinstance(s, dict) else None for s in scenes] != expected_ids:
        raise PlanError('Plan scene IDs and order must match storyboard exactly')
    for idx, scene in enumerate(scenes):
        if set(scene) != {'scene_id', 'motion', 'motion_intensity', 'transition_to_next', 'transition_length'}:
            raise PlanError(f'Unexpected/missing fields for scene {scene["scene_id"]}')
        if (scene['motion'] not in MOTIONS or scene['motion_intensity'] not in INTENSITIES
            or scene['transition_to_next'] not in TRANSITIONS):
            raise PlanError(f'Invalid motion/intensity/transition for scene {scene["scene_id"]}')
        if scene['transition_to_next'] == 'none':
            if scene['transition_length'] is not None:
                raise PlanError('none transition requires null transition_length')
        elif scene['transition_length'] not in LENGTHS:
            raise PlanError('Transition length must be short, medium or long')
        if project.storyboard['scenes'][idx].get('type') in ('image_sequence','video_clip') and scene['motion'] != 'static':
            raise PlanError('image_sequence scenes require static motion')
        if idx == len(scenes)-1 and scene['transition_to_next'] != 'none':
            raise PlanError('Last scene transition must be none')
    if require_current:
        if plan_cache_key(project, plan['style'], meta['seed']) != meta['cache_key']:
            raise PlanError('Stale plan: storyboard or configuration has changed')
    return plan


def require_approved_plan(project: Project, plan: dict[str, Any]) -> None:
    """Respect an opt-in manual approval gate on all render-producing commands."""
    if (project.config.get('planning',{}).get('require_plan_approval',False)
        and not plan['plan_meta'].get('approved_by_user',False)):
        raise PlanError('Editing plan requires approval: run ai-video-editor approve-plan PROJECT')


def get_default_editing_plan(project: Project, *, style: str | None = None,
                             seed: int | None = None, write_output: bool = True) -> dict[str, Any]:
    style, seed = _parameters(project, style, seed)
    preset = STYLE_PRESETS[style]
    rng = random.Random(seed)
    scene_plan = []
    for index, scene in enumerate(project.storyboard['scenes']):
        motion = 'static' if scene.get('type') in ('image_sequence','video_clip') else rng.choice(preset['motions'])
        intensity = 'subtle' if scene.get('type') in ('image_sequence','video_clip') else rng.choice(preset['intensities'])
        transition = 'none' if index == len(project.storyboard['scenes'])-1 else rng.choice(preset['transitions'])
        scene_plan.append({
            'scene_id': scene['scene_id'], 'motion': motion,
            'motion_intensity': intensity, 'transition_to_next': transition,
            'transition_length': preset['length'] if transition != 'none' else None,
        })
    result = {
        'schema_version': '1.1',
        'plan_meta': {
            'source': 'rule_based', 'provider': None, 'model': None,
            'prompt_version': PROMPT_VERSION,
            'cache_key': plan_cache_key(project, style, seed),
            'revisions_used': 0, 'approved_by_user': False, 'seed': seed,
        },
        'style': style, 'scenes': scene_plan,
    }
    validate_editing_plan(project, result)
    if write_output:
        atomic_write_json(project.folder / 'editing_plan.json', result)
    return result


def merge_plan_into_storyboard(project: Project, plan: dict[str, Any]) -> dict[str, Any]:
    """Return a new copy; do not modify the source or allow narration/asset editing."""
    import copy
    validate_editing_plan(project, plan)
    result = copy.deepcopy(project.storyboard)
    for scene, choices in zip(result['scenes'], plan['scenes']):
        scene['editing'] = {k: choices[k] for k in ('motion', 'motion_intensity',
                                                  'transition_to_next', 'transition_length')}
    return result
