"""Phase 2.3: reviewable AI scene decisions and Core-compatible editing plans.

Planning only: no media generation, project asset mutation or implicit approval.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib import request, error
from typing import Any, Callable

from ..project import Project, ValidationError, atomic_write_json, read_json
from ..planner import (MOTIONS, INTENSITIES, TRANSITIONS, LENGTHS, STYLES,
                       plan_cache_key, validate_editing_plan)
from .script_generator import load_script_draft, digest, _extract_response_text
from .manifest import validate_brief

FILENAME = 'agent_scene_plan.json'
BRIEF_NAME = 'agent_scene_brief.json'
PROMPT_VERSION = 'ai-scene-v2.3.0'
ALLOWED_MEDIA = {'image', 'video_clip'}

DEFAULT_SCENE_INSTRUCTIONS = (
    'You are an assistant video scene editor. Propose media types, cinematic motions, transitions, '
    'and brief English media prompts for the approved script. Do NOT fabricate existing files. '
    'Use image or video_clip only. Motion on video_clip MUST be static. '
    'The last scene must have transition none with null length; other none transitions also have null length. '
    'Durations are approximate and may not match real voice or media. '
    'Only propose; user review is required.'
)


def scene_instructions(value: str | None = None) -> str:
    if value is None:
        return DEFAULT_SCENE_INSTRUCTIONS
    if not isinstance(value, str) or not 10 <= len(value.strip()) <= 12000:
        raise ValidationError('Scene Plan instructions must be 10..12000 characters')
    return value.strip()



def _approved_script(project: Project) -> dict[str, Any]:
    draft = load_script_draft(project)
    if draft.get('status') != 'SCRIPT_APPROVED' or draft.get('approved_hash') != digest(draft['content']):
        raise ValidationError('Phase 2.2 script must be approved and unchanged')
    return draft


def scene_schema() -> dict[str, Any]:
    choices = {
        'scene_id': {'type': 'string'},
        'media_type': {'type':'string','enum':sorted(ALLOWED_MEDIA)},
        'asset_prompt_en': {'type':'string'},
        'estimated_duration_seconds': {'type':'number'},
        'motion': {'type':'string','enum': sorted(MOTIONS)},
        'motion_intensity': {'type':'string','enum':sorted(INTENSITIES)},
        'transition_to_next': {'type':'string','enum':sorted(TRANSITIONS)},
        'transition_length': {'type':['string','null'],'enum':[None,*sorted(LENGTHS)]},
        'reason': {'type':'string'},
    }
    scene={'type':'object','additionalProperties':False,
           'properties':choices,'required':list(choices)}
    return {'type':'object','additionalProperties':False,
            'properties':{'style':{'type':'string','enum':sorted(STYLES)},
                          'scenes':{'type':'array','items':scene}},
            'required':['style','scenes']}


def validate_proposal(content: Any, script: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content,dict) or set(content) != {'style','scenes'} or content['style'] not in STYLES:
        raise ValidationError('Invalid Phase 2.3 proposal structure/style')
    scenes=content['scenes']
    original=script['scenes']
    if not isinstance(scenes,list) or len(scenes)!=len(original):
        raise ValidationError('Proposal scene count differs from approved script')
    required=set(scene_schema()['properties']['scenes']['items']['properties'])
    for index, (scene, base) in enumerate(zip(scenes,original)):
        if not isinstance(scene,dict) or set(scene)!=required or scene['scene_id']!=base['scene_id']:
            raise ValidationError(f'Proposal scene order/schema mismatch at {index+1}')
        kind=scene['media_type']
        if kind not in ALLOWED_MEDIA:
            raise ValidationError('Invalid media type')
        if kind == 'image' and not base['narration'].strip():
            raise ValidationError('Image media requires narration from the approved script')
        if not isinstance(scene['asset_prompt_en'],str) or not scene['asset_prompt_en'].strip():
            raise ValidationError('Media generation prompt missing')
        if not isinstance(scene['reason'],str) or not scene['reason'].strip():
            raise ValidationError('Scene reasoning required')
        d=scene['estimated_duration_seconds']
        if type(d) not in (int,float) or not 0 < d <= 600:
            raise ValidationError('Invalid scene duration proposal')
        if scene['motion'] not in MOTIONS or scene['motion_intensity'] not in INTENSITIES:
            raise ValidationError('Invalid motion/intensity')
        if kind == 'video_clip' and scene['motion']!='static':
            raise ValidationError('video_clip motion must be static')
        trans=scene['transition_to_next']; length=scene['transition_length']
        if trans not in TRANSITIONS or (length is not None and length not in LENGTHS):
            raise ValidationError('Invalid transition/length')
        if (trans=='none') != (length is None):
            raise ValidationError('Transition none requires null length and vice versa')
        if index==len(scenes)-1 and trans!='none':
            raise ValidationError('Last scene transition must be none')
    return content


def generate_openai_scene_plan(script: dict[str, Any], *, model: str='gpt-4.1-mini',
                               api_key: str | None=None, opener: Callable[...,Any]|None=None,
                               timeout: int=120, base_url: str='https://api.openai.com/v1',
                               instructions: str | None = None) -> tuple[dict[str,Any],dict[str,Any]]:
    key=(api_key if api_key is not None else (os.environ.get('OPENAI_API_KEY') if base_url.rstrip('/') == 'https://api.openai.com/v1' else None))
    if not key:
        raise ValidationError('OPENAI_API_KEY missing; use --import-json for offline mode')
    body={
        'model':model, 'store':False,
        'instructions':scene_instructions(instructions),
        'input':json.dumps(script,ensure_ascii=False),
        'text':{'format':{'type':'json_schema','name':'scene_plan','strict':True,'schema':scene_schema()}},
    }
    req=request.Request(base_url.rstrip('/') + '/responses',data=json.dumps(body,ensure_ascii=False).encode(),
                        headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},method='POST')
    try:
        with (opener or request.urlopen)(req,timeout=timeout) as resp:
            raw=resp.read()
    except error.HTTPError as exc:
        raise ValidationError(f'OpenAI HTTP {exc.code}; check model, key and quota') from exc
    except (error.URLError,OSError,TimeoutError) as exc:
        raise ValidationError(f'OpenAI connection failed: {type(exc).__name__}') from exc
    try:
        payload=json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('response payload must be an object')
        result=json.loads(_extract_response_text(payload))
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        raise ValidationError('Invalid OpenAI scene-plan JSON') from exc
    validate_proposal(result,script)
    usage=payload.get('usage') or {}
    return result,{'provider':'openai','model':model,
                   'input_tokens':int(usage.get('input_tokens') or 0),
                   'output_tokens':int(usage.get('output_tokens') or 0),
                   'total_tokens':int(usage.get('total_tokens') or 0),
                   'estimated_cost_usd':None}


def create_scene_plan(project: Project, content: dict[str,Any], *, source: str='manual',
                      usage: dict[str,Any]|None=None, replace: bool=False) -> dict[str,Any]:
    draft=_approved_script(project)
    validate_proposal(content,draft['content'])
    path=project.folder/FILENAME
    if path.exists() and not replace:
        raise ValidationError('Scene plan already exists; use --replace explicitly')
    document={'schema_version':'2.3','project_id':project.project_id,'status':'AWAITING_SCENE_PLAN_APPROVAL',
              'source':source,'script_hash':draft['approved_hash'],'content':content,
              'approved_hash':None,'usage':usage or {'provider':'manual','input_tokens':0,
                                                       'output_tokens':0,'total_tokens':0,
                                                       'estimated_cost_usd':0}}
    atomic_write_json(path,document)
    return document


def load_scene_plan(project: Project, *, approved: bool=False) -> dict[str,Any]:
    draft=_approved_script(project)
    doc=read_json(project.folder/FILENAME)
    if doc.get('schema_version')!='2.3' or doc.get('project_id')!=project.project_id or doc.get('script_hash')!=draft['approved_hash']:
        raise ValidationError('Scene plan missing, invalid or stale after script changes')
    validate_proposal(doc.get('content'),draft['content'])
    if approved and (doc.get('status')!='SCENE_PLAN_APPROVED' or doc.get('approved_hash')!=digest(doc['content'])):
        raise ValidationError('Scene plan requires approval or changed after approval')
    return doc


def approve_scene_plan(project: Project) -> dict[str,Any]:
    doc=load_scene_plan(project)
    doc['approved_hash']=digest(doc['content'])
    doc['status']='SCENE_PLAN_APPROVED'
    atomic_write_json(project.folder/FILENAME,doc)
    return {'project_id':project.project_id,'status':doc['status'],'scenes':len(doc['content']['scenes'])}


def export_scene_brief(project:Project,*,replace:bool=False) -> dict[str,Any]:
    doc=load_scene_plan(project,approved=True)
    script=_approved_script(project)['content']
    brief={'title':script['title'], 'scenes':[{'scene_id':orig['scene_id'],
            'type':plan['media_type'], 'narration':orig['narration'],
            'prompt':plan['asset_prompt_en']} for orig,plan in zip(script['scenes'],doc['content']['scenes'])]}
    validate_brief(brief)
    path=project.folder/BRIEF_NAME
    if path.exists() and not replace:
        raise ValidationError('Scene brief exists; use --replace explicitly')
    atomic_write_json(path,brief)
    return {'project_id':project.project_id,'file':str(path),'scenes':len(brief['scenes']),'status':'BRIEF_READY'}


def build_scene_editing_plan(project: Project) -> dict[str, Any]:
    """Generate/validate AI editing decisions without touching the on-disk plan."""
    doc = load_scene_plan(project, approved=True)
    proposed = doc['content']['scenes']
    actual = project.storyboard['scenes']
    if len(actual) != len(proposed) or any(
            s['scene_id'] != p['scene_id'] or s.get('type', 'image') != p['media_type']
            for s, p in zip(actual, proposed)):
        raise ValidationError('Storyboard scene IDs/types differ from proposal. Export scene brief, bind assets and compile first')
    style = doc['content']['style']
    seed = project.config['planning']['seed']
    result = {'schema_version': '1.1', 'plan_meta': {
        'source': 'ai_assisted', 'provider': doc['usage'].get('provider'),
        'model': doc['usage'].get('model'), 'prompt_version': PROMPT_VERSION,
        'cache_key': plan_cache_key(project, style, seed), 'revisions_used': 0,
        'approved_by_user': False, 'seed': seed},
        'style': style,
        'scenes': [{'scene_id': decision['scene_id'], 'motion': decision['motion'],
                    'motion_intensity': decision['motion_intensity'],
                    'transition_to_next': decision['transition_to_next'],
                    'transition_length': decision['transition_length']}
                   for decision in proposed]}
    validate_editing_plan(project, result)
    return result


def apply_scene_plan(project:Project,*,replace:bool=False) -> dict[str,Any]:
    """Apply only when current Core storyboard matches proposed scene IDs/media types.

    Never edits media references, narration, storyboard or approves a Core editing plan.
    """
    path = project.folder / 'editing_plan.json'
    if path.exists() and not replace:
        raise ValidationError('Editing plan exists; use --replace only after review (never overwrites silently)')
    result = build_scene_editing_plan(project)
    atomic_write_json(path,result)
    return {'project_id':project.project_id,'status':'EDITING_PLAN_CREATED','source':'ai_assisted',
            'file':str(path),'approved_by_user':False,'scenes':len(result['scenes'])}
