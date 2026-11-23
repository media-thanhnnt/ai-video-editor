"""Local-only HTTP API. Calls ProjectManager directly; CLI remains independent.

Phase 2.4.1 intentionally exposes project discovery/creation and read-only workflow status.
No media upload, arbitrary filesystem path, shell commands or FFmpeg invocation.
"""
from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timezone
from uuid import uuid4
from shutil import move
import hashlib
import re
from fastapi import UploadFile, File, Form
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from ..agent.script_generator import (DRAFT_NAME, BRIEF_NAME, openai_generate, create_script_draft,
                                      load_script_draft, approve_script, export_agent_brief,
                                      validate_script, digest)
from ..agent.scene_planner import (
    FILENAME as SCENE_NAME, BRIEF_NAME as SCENE_BRIEF_NAME,
    generate_openai_scene_plan, create_scene_plan, load_scene_plan,
    validate_proposal, approve_scene_plan, export_scene_brief,
    scene_instructions, DEFAULT_SCENE_INSTRUCTIONS, scene_schema,
)
from ..agent.asset_workflow import (make_prompt_pack, render_prompt_markdown, generate_openai_asset,
                                    save_generated_asset, output_aspect_ratio, openai_image_size)
from .provider_settings import (get_key, save_key, delete_key, load_choices, save_choices, public_settings, CATALOG,
                                add_provider, add_model, delete_model, delete_provider, provider_info)
from .gemini_provider import generate_text as gemini_text, generate_asset as gemini_asset
from .voice_settings import (options as voice_options, load as load_voice, save as save_voice,
                             validate_voice, default_voice)
from .jobs import JobManager
from .limits import usage as api_usage, reserve as reserve_api_attempt, set_limits as set_api_limits
from .workflow import prepare_manifest, candidates, bind_generated, prepare_render, approve_plan, render_cli
from ..agent.manifest import load_manifest, bind_asset, missing_assets
from ..project import atomic_write_json

from ..project import ProjectManager, ValidationError, PROJECT_ID, read_json


class ProviderKeyRequest(BaseModel):
    key: str = Field(min_length=8, max_length=512)

class ProviderCreateRequest(BaseModel):
    provider_id: str = Field(min_length=2, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=10, max_length=300)
    adapter: str = 'openai_compatible'

class ProviderModelCreateRequest(BaseModel):
    provider_id: str
    task: str
    model: str

class ProviderChoicesRequest(BaseModel):
    choices: dict[str, dict[str, str]]

class BatchAssetsRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    kind: str = Field(pattern="^(image|audio)$")
    voice: str | None = Field(default=None, max_length=40)
    confirmed: bool = False

class VoicePreferenceRequest(BaseModel):
    voice: str = Field(min_length=1, max_length=40)


class ApiLimitsRequest(BaseModel):
    image: int = Field(ge=0)
    audio: int = Field(ge=0)


class BindAssetRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern='^(media|voice|overlay)$')
    file: str = Field(min_length=1, max_length=500)


class OverlayRequest(BaseModel):
    scene_ids: list[str] | None = None
    bind: bool = True


class RenderPreparationRequest(BaseModel):
    approve_manifest: bool = False
    replace_stale_plan: bool = False


class RenderRequest(BaseModel):
    confirmed: bool = False
    timeout_minutes: int = Field(default=0, ge=0, le=525600)  # 0 disables wall-clock timeout


class GenerateAssetRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern='^(image|audio|video)$')
    model: str | None = Field(default=None, max_length=100)
    voice: str | None = Field(default=None, max_length=40)
    confirmed: bool = False


class NewProject(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)


class GenerateScriptRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=12000)
    model: str = Field(default='gpt-4.1-mini', min_length=1, max_length=100)
    replace: bool = False


class UpdateScriptRequest(BaseModel):
    content: dict[str, Any]


class ExportScriptRequest(BaseModel):
    replace: bool = False


class ImportScriptRequest(BaseModel):
    content: dict[str, Any]
    replace: bool = False



class GenerateSceneRequest(BaseModel):
    instructions: str | None = Field(default=None, min_length=10, max_length=12000)
    model: str = Field(default='gpt-4.1-mini', min_length=1, max_length=100)
    replace: bool = False


class UpdateSceneRequest(BaseModel):
    content: dict[str, Any]
    replace_downstream: bool = False


class ExportSceneRequest(BaseModel):
    replace: bool = False


def _asset_prompt(scene: dict, kind: str) -> str:
    # Voice MUST use the script's spoken narration, never the image/video prompt.
    prompt = (scene.get('audio_prompt') if kind == 'audio' else
              (scene.get('image_prompt') or scene.get('video_prompt')))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValidationError(f'No {kind} prompt/narration for scene {scene.get("scene_id")}')
    if kind == 'audio' and prompt == (scene.get('image_prompt') or scene.get('video_prompt')):
        raise ValidationError('Audio narration unexpectedly matches the media prompt; review Script before TTS')
    return prompt.strip()


def _provider_key(projects_dir: Path, provider: str) -> str:
    key = get_key(provider, projects_dir)
    if not key:
        raise ValidationError(f'Configure API key for {provider} before generation; no quota consumed')
    return key


def _safe_script_error(exc: ValidationError) -> HTTPException:
    message = str(exc)
    return HTTPException(409 if any(v in message for v in ('exists', 'already', 'approved', 'changed')) else 422, message)


def _metadata(project) -> dict:
    folder = project.folder
    def stage(filename: str, key: str) -> dict:
        path = folder / filename
        if not path.is_file():
            return {'status': 'NOT_STARTED'}
        try:
            payload = read_json(path)
            status = payload.get('status')
            if not isinstance(status, str):
                raise ValueError('Missing status')
            out = {'status': status}
            if isinstance(payload.get('usage'), dict):
                out['usage'] = {k:payload['usage'].get(k) for k in ('model','total_tokens')}
            if key == 'script':
                content = payload.get('content', {})
                if isinstance(content, dict) and isinstance(content.get('scenes'), list):
                    out['scenes'] = len(content['scenes'])
            return out
        except (OSError, ValueError, TypeError):
            return {'status': 'INVALID'}

    manifest = stage('agent_manifest.json', 'manifest')
    return {
        'project_id': project.project_id,
        'title': project.storyboard['title'],
        'core_status': project.state['status'],
        'storyboard_scenes': len(project.storyboard['scenes']),
        'script': stage('agent_script_draft.json', 'script'),
        'scene_plan': stage('agent_scene_plan.json', 'scene'),
        'assets': manifest,
        'has_asset_brief': (folder / 'agent_scene_brief.json').is_file(),
        'has_editing_plan': (folder / 'editing_plan.json').is_file(),
        'has_output': (folder / 'output' / 'final.mp4').is_file() and not (folder / 'script_revision_pending.json').exists(),
        'script_revision_pending': (folder / 'script_revision_pending.json').exists(),
    }


def create_app(*, projects_dir: Path, default_config: Path, static_dir: Path | None = None,
               testing: bool = False) -> FastAPI:
    manager = ProjectManager(projects_dir, default_config)
    jobs = JobManager(workers=2)
    submit_lock = Lock()
    app = FastAPI(title='AI Video Editor Local API', version='2.5.1.3', docs_url='/api/docs', redoc_url=None,
                  openapi_url='/api/openapi.json')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=
                       ['127.0.0.1', 'localhost', '[::1]'] + (['testserver'] if testing else []))
    # A browser on another website must not be able to create local projects.
    @app.middleware('http')
    async def local_origin_check(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin:
                from urllib.parse import urlsplit
                try:
                    parsed = urlsplit(origin)
                    origin_port = parsed.port
                except ValueError:
                    return JSONResponse({'detail': 'Invalid Origin'}, status_code=403)
                if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                        or origin_port != request.url.port):
                    return JSONResponse({'detail': 'Cross-origin mutations are blocked'}, status_code=403)
        return await call_next(request)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'phase': '2.5.1.3'}

    @app.get('/api/provider-settings')
    def provider_settings_get():
        try:
            return public_settings(projects_dir)
        except (ValidationError, ValueError, OSError, TypeError, KeyError) as exc:
            raise HTTPException(422, 'Invalid provider catalog/settings configuration') from exc

    @app.put('/api/provider-settings')
    def provider_settings_put(body: ProviderChoicesRequest):
        try:
            save_choices(projects_dir, body.choices)
            return public_settings(projects_dir)
        except ValidationError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.put('/api/provider-settings/keys/{provider}')
    def provider_key_put(provider: str, body: ProviderKeyRequest):
        try:
            save_key(provider,body.key,projects_dir)
            return {'provider':provider,'has_key':True}
        except ValidationError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.delete('/api/provider-settings/keys/{provider}')
    def provider_key_delete(provider: str):
        try:
            delete_key(provider,projects_dir)
            return {'provider':provider,'has_key':bool(get_key(provider,projects_dir))}
        except ValidationError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post('/api/provider-settings/providers', status_code=201)
    def provider_create(body: ProviderCreateRequest):
        try:
            add_provider(projects_dir, provider_id=body.provider_id, label=body.label,
                         base_url=body.base_url, adapter=body.adapter)
            return public_settings(projects_dir)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete('/api/provider-settings/providers/{provider}')
    def provider_remove(provider: str):
        try:
            delete_provider(projects_dir, provider)
            return public_settings(projects_dir)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/provider-settings/models', status_code=201)
    def model_create(body: ProviderModelCreateRequest):
        try:
            add_model(projects_dir, provider_id=body.provider_id, task=body.task, model=body.model)
            return public_settings(projects_dir)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete('/api/provider-settings/models/{task}/{provider}/{model:path}')
    def model_remove(task: str, provider: str, model: str):
        try:
            delete_model(projects_dir, provider_id=provider, task=task, model=model)
            return public_settings(projects_dir)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    def _voice_context(project_id: str):
        project = _get_project(project_id)
        choice = load_choices(projects_dir)['audio']
        provider = choice['provider']
        adapter = provider_info(projects_dir, provider)['adapter']
        return project, provider, adapter, choice['model']

    @app.get('/api/projects/{project_id}/assets/voice-settings')
    def voice_settings_get(project_id: str):
        try:
            project, provider, adapter, model = _voice_context(project_id)
            return {'provider': provider, 'model': model,
                    'voices': voice_options(provider, adapter),
                    'selected_voice': load_voice(project, provider, adapter)}
        except (ValidationError, ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put('/api/projects/{project_id}/assets/voice-settings')
    def voice_settings_put(project_id: str, body: VoicePreferenceRequest):
        try:
            project, provider, adapter, model = _voice_context(project_id)
            selected = save_voice(project, provider, adapter, body.voice)
            return {'provider': provider, 'model': model,
                    'voices': voice_options(provider, adapter), 'selected_voice': selected}
        except (ValidationError, ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def _call_asset_provider(project, kind, prompt, choice, key, voice, instructions=None):
        """Single paid call shared by the sync, job and batch routes (identical parameters everywhere)."""
        info = provider_info(projects_dir, choice['provider'])
        ratio = output_aspect_ratio(project)
        if info['adapter'] in ('openai', 'openai_compatible'):
            return generate_openai_asset(kind, prompt, model=choice['model'],
                voice=voice or default_voice(choice['provider'], info['adapter']), api_key=key,
                base_url=info['base_url'], compatible=info['adapter'] == 'openai_compatible',
                size=openai_image_size(ratio), instructions=instructions)
        return gemini_asset(kind, prompt, choice['model'], voice=voice or 'Kore',
            aspect_ratio=ratio if kind == 'image' else None,
            **({'projects_dir': projects_dir} if choice['model'] not in CATALOG[kind]['gemini'] else {}))

    def _chosen_voice(project, kind, requested, provider):
        if kind != 'audio':
            return None
        adapter = provider_info(projects_dir, provider)['adapter']
        value = requested if requested is not None else load_voice(project, provider, adapter)
        return validate_voice(value, provider, adapter)

    @app.get('/api/projects')
    def list_projects():
        projects = []
        for pid in manager.list_projects():
            try:
                projects.append(_metadata(manager.load_project(pid)))
            except (ValidationError, OSError, ValueError) as exc:
                projects.append({'project_id':pid, 'title':pid, 'error':str(exc)})
        return {'projects': projects, 'count': len(projects)}

    @app.get('/api/projects/{project_id}')
    def project_detail(project_id: str):
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(400, 'Invalid project ID')
        try:
            return _metadata(manager.load_project(project_id))
        except ValidationError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post('/api/projects', status_code=201)
    def create_project(body: NewProject):
        if not PROJECT_ID.fullmatch(body.project_id):
            raise HTTPException(422, 'Project ID: 1-64 ASCII letters, digits, _ or -')
        try:
            project = manager.create_project(body.project_id, body.title)
            return _metadata(project)
        except ValidationError as exc:
            raise HTTPException(409 if 'already exists' in str(exc) else 422, str(exc)) from exc

    def _get_project(project_id: str):
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(400, 'Invalid project ID')
        try:
            return manager.load_project(project_id)
        except ValidationError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/projects/{project_id}/script')
    def get_script(project_id: str):
        project = _get_project(project_id)
        if not (project.folder / DRAFT_NAME).exists():
            raise HTTPException(404, 'Script draft not created')
        try:
            return load_script_draft(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, 'Script draft invalid') from exc

    @app.post('/api/projects/{project_id}/script/generate', status_code=201)
    def generate_script(project_id: str, body: GenerateScriptRequest):
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for current project job to finish before replacing Script')
        # Never bill the user for an API call if it cannot be saved.
        if (project.folder / DRAFT_NAME).exists() and not body.replace:
            raise HTTPException(409, 'Script already exists; explicitly allow replacement')
        # Downstream approved plans would become stale; leave them untouched.
        try:
            selection=load_choices(projects_dir)['script']
            if provider_info(projects_dir,selection['provider'])['adapter'] in ('openai','openai_compatible'):
                content, usage = openai_generate(body.requirement.strip(), model=selection['model'],
                    api_key=_provider_key(projects_dir,selection['provider']),
                    **({'base_url':provider_info(projects_dir,selection['provider'])['base_url']}
                       if selection['provider']!='openai' else {}))
                usage['provider']=selection['provider']
            else:
                content, usage = gemini_text('script',body.requirement.strip(),selection['model'],
                    **({'projects_dir':projects_dir} if selection['model'] not in CATALOG['script']['gemini'] else {}))
            prior_script = read_json(project.folder / DRAFT_NAME) if body.replace and (project.folder / DRAFT_NAME).exists() else None
            draft = create_script_draft(project, content, source=selection['provider'], brief=body.requirement.strip(),
                                       usage=usage, replace=body.replace)
            if body.replace:
                _archive_script_dependents(project, archive_script=False)
                if prior_script is not None:
                    history = project.folder / 'history'
                    history.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(history / ('previous_script_' + uuid4().hex[:12] + '.json'), prior_script)
            return draft
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, 'Script generation returned invalid data') from exc

    def _archive_script_dependents(project, *, archive_script=True):
        """Retire derived files after an explicitly changed script; never delete media/output.

        Files are moved to a revision snapshot so the new script cannot accidentally
        reuse a previously approved Scene Plan, manifest or editing plan.
        """
        names = ('agent_brief_generated.json', 'agent_scene_plan.json',
                 'agent_scene_brief.json', 'agent_manifest.json',
                 'editing_plan.json', 'timeline.json')
        existing = [project.folder / name for name in names if (project.folder / name).is_file()]
        if not existing and not (project.folder / 'output' / 'final.mp4').exists():
            return
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
        destination = project.folder / 'history' / ('script_revision_' + stamp)
        destination.mkdir(parents=True, exist_ok=False)
        from shutil import copy2
        if archive_script and (project.folder / DRAFT_NAME).is_file():
            copy2(project.folder / DRAFT_NAME, destination / 'previous_agent_script_draft.json')
        for path in existing:
            move(str(path), str(destination / path.name))
        atomic_write_json(project.folder / 'script_revision_pending.json', {
            'revision': stamp, 'archived_files': [p.name for p in existing],
            'previous_output_retained': (project.folder / 'output' / 'final.mp4').exists(),
            'message': 'Script was updated. Regenerate and approve downstream plans before rendering.'
        })

    @app.put('/api/projects/{project_id}/script')
    def update_script(project_id: str, body: UpdateScriptRequest):
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for current project job to finish before editing Script')
        try:
            draft = load_script_draft(project)
            validate_script(body.content)
            if digest(body.content) != digest(draft['content']):
                _archive_script_dependents(project)
            # Edits always revoke approval and require a new explicit approval.
            draft['content'] = body.content
            draft['approved_hash'] = None
            draft['status'] = 'AWAITING_SCRIPT_APPROVAL'
            atomic_write_json(project.folder / DRAFT_NAME, draft)
            return draft
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.get('/api/projects/{project_id}/script/editable-export')
    def export_editable_script(project_id: str):
        """Return portable editable script; never export API keys, approval hashes or usage."""
        project = _get_project(project_id)
        try:
            draft = load_script_draft(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, 'Cannot export invalid script') from exc
        return {'format': 'ai-video-editor-script', 'format_version': 1,
                'content': draft['content']}

    @app.post('/api/projects/{project_id}/script/import')
    def import_editable_script(project_id: str, body: ImportScriptRequest):
        """Import an edited export. On changes, retire downstream artifacts and approvals."""
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for current project job to finish before importing Script')
        try:
            # Accept only the content object, not arbitrary caller-supplied approval/status fields.
            content = body.content
            if content.get('format') == 'ai-video-editor-script':
                if content.get('format_version') != 1 or not isinstance(content.get('content'), dict):
                    raise ValidationError('Unsupported editable Script JSON format')
                content = content['content']
            elif 'content' in content and 'schema_version' in content:
                # Full agent_script_draft.json can also be imported; ignore its approval metadata.
                if not isinstance(content['content'], dict):
                    raise ValidationError('Script content must be a JSON object')
                content = content['content']
            path = project.folder / DRAFT_NAME
            # The existing UI's "Export Brief" format is intentionally minimal.
            # Allow round-trip edits to its title / scene type / narration / prompt
            # while retaining durations, language, style and summary from the
            # original Script. Scene IDs must be identical to avoid mismatching assets.
            if ('content' not in content and 'title' in content and
                    isinstance(content.get('scenes'), list) and
                    'language' not in content):
                if not path.is_file():
                    raise ValidationError('Importing an Asset Brief requires an existing Script')
                current = load_script_draft(project)['content']
                old_scenes = current['scenes']
                imported = content['scenes']
                if ([v.get('scene_id') for v in imported if isinstance(v, dict)] !=
                        [v['scene_id'] for v in old_scenes] or len(imported) != len(old_scenes)):
                    raise ValidationError('Brief scene IDs/order must match the current Script; use editable Script JSON to add/remove scenes')
                revised = dict(current)
                revised['title'] = content['title']
                revised['scenes'] = [
                    {**old, **{key: row[key] for key in ('type', 'narration', 'prompt') if key in row}}
                    for old, row in zip(old_scenes, imported)
                ]
                if any(not isinstance(row.get('narration'), str) for row in revised['scenes']):
                    raise ValidationError('Brief scene narration must be a string')
                revised['script'] = ' '.join(row['narration'].strip() for row in revised['scenes'] if row['narration'].strip())
                content = revised
            validate_script(content)
            if path.is_file():
                old = load_script_draft(project)
                if digest(content) == digest(old['content']):
                    return {'changed': False, 'draft': old}
                if not body.replace:
                    raise HTTPException(409, 'Script exists; confirm replacement to import edited Script')
                _archive_script_dependents(project)
                old['content'] = content
                old['approved_hash'] = None
                old['status'] = 'AWAITING_SCRIPT_APPROVAL'
                atomic_write_json(path, old)
                draft = old
            else:
                draft = create_script_draft(project, content, source='manual', brief='Imported editable Script JSON')
            return {'changed': True, 'draft': draft}
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.post('/api/projects/{project_id}/script/approve')
    def approve_script_route(project_id: str):
        project = _get_project(project_id)
        try:
            return approve_script(project)
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.post('/api/projects/{project_id}/script/export')
    def export_script_route(project_id: str, body: ExportScriptRequest):
        project = _get_project(project_id)
        try:
            return export_agent_brief(project, replace=body.replace)
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    def _scene_guard(project):
        # Do not silently invalidate artifacts already generated from an older plan.
        conflicts = ('agent_scene_brief.json', 'agent_manifest.json',
                     'editing_plan.json')
        existing = [name for name in conflicts if (project.folder / name).exists()]
        if existing:
            raise HTTPException(409, 'Downstream files exist; scene plan is locked: ' + ', '.join(existing))

    @app.get('/api/projects/{project_id}/scene-plan/prompt-preview')
    def scene_prompt_preview(project_id: str):
        project = _get_project(project_id)
        try:
            approved = load_script_draft(project)
            if approved.get('status') != 'SCRIPT_APPROVED' or approved.get('approved_hash') != digest(approved.get('content')):
                raise ValidationError('Approve unchanged Script before creating Scene Plan')
            selection = load_choices(projects_dir)['scene_plan']
            info = provider_info(projects_dir, selection['provider'])
            input_text = json.dumps(approved['content'], ensure_ascii=False)
            if info['adapter'] in ('openai', 'openai_compatible'):
                instructions = DEFAULT_SCENE_INSTRUCTIONS
                wire_format = 'OpenAI Responses API: instructions + input JSON'
            elif info['adapter'] == 'gemini':
                instructions = ('Make scene plan with motion, media prompts, transitions; '
                                'video_clip motion static, last transition none / null length. Schema: '
                                + json.dumps(scene_schema()))
                wire_format = 'Gemini generateContent: instructions + INPUT JSON'
            else:
                raise ValidationError('Unsupported scene planning adapter')
            return {'provider': selection['provider'], 'model': selection['model'],
                    'adapter': info['adapter'], 'instructions': instructions,
                    'script_input': input_text, 'wire_format': wire_format,
                    'gemini_input_suffix': '\nINPUT:\n' if info['adapter'] == 'gemini' else ''}
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.get('/api/projects/{project_id}/scene-plan')
    def get_scene_plan(project_id: str):
        project = _get_project(project_id)
        if not (project.folder / SCENE_NAME).is_file():
            raise HTTPException(404, 'Scene plan not created')
        try:
            return load_scene_plan(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, 'Scene plan invalid or stale: ' + str(exc)) from exc

    @app.post('/api/projects/{project_id}/scene-plan/generate', status_code=201)
    def generate_scene_plan(project_id: str, body: GenerateSceneRequest):
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for the current project job to finish before generating Scene Plan')
        if (project.folder / SCENE_NAME).exists() and not body.replace:
            raise HTTPException(409, 'Scene plan exists; explicitly allow replacement')
        _scene_guard(project)
        try:
            approved = load_script_draft(project)
            if approved.get('status') != 'SCRIPT_APPROVED' or approved.get('approved_hash') != digest(approved.get('content')):
                raise ValidationError('Approve unchanged Script before creating Scene Plan')
            selection=load_choices(projects_dir)['scene_plan']
            if provider_info(projects_dir,selection['provider'])['adapter'] in ('openai','openai_compatible'):
                content, usage = generate_openai_scene_plan(approved['content'], model=selection['model'],
                    **({'instructions': scene_instructions(body.instructions)} if body.instructions is not None else {}),
                    api_key=_provider_key(projects_dir,selection['provider']),
                    **({'base_url':provider_info(projects_dir,selection['provider'])['base_url']}
                       if selection['provider']!='openai' else {}))
                usage['provider']=selection['provider']
            else:
                import json
                content, usage = gemini_text('scene_plan',json.dumps(approved['content'],ensure_ascii=False),selection['model'],
                    **({'instructions_override': scene_instructions(body.instructions)} if body.instructions is not None else {}),
                    **({'projects_dir':projects_dir} if selection['model'] not in CATALOG['scene_plan']['gemini'] else {}))
            return create_scene_plan(project, content, source=selection['provider'], usage=usage, replace=body.replace)
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, 'Scene generation returned invalid data') from exc

    @app.put('/api/projects/{project_id}/scene-plan')
    def update_scene_plan(project_id: str, body: UpdateSceneRequest):
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for the current project job to finish before editing Scene Plan')
        try:
            document = load_scene_plan(project)
            script = load_script_draft(project)
            validate_proposal(body.content, script['content'])
            if digest(body.content) == digest(document['content']):
                return document  # no-op preserves approval and downstream artifacts
            # render_checkpoint.json lives in reports/ and is invalidated by the render identity
            # (plan/timeline hashes), so it does not need to be listed here.
            conflicts = ('agent_scene_brief.json', 'agent_manifest.json', 'storyboard.json',
                         'editing_plan.json', 'timeline.json')
            existing = [project.folder / name for name in conflicts if (project.folder / name).is_file()]
            # A fresh project has an empty placeholder storyboard: it is not a
            # downstream result of Scene Planning and must not lock edits.
            storyboard_path = project.folder / 'storyboard.json'
            if storyboard_path in existing and not read_json(storyboard_path).get('scenes'):
                existing.remove(storyboard_path)
            if existing and not body.replace_downstream:
                raise HTTPException(409, 'Scene Plan has downstream artifacts. Confirm revision and re-approve; files: '
                                    + ', '.join(path.name for path in existing))
            revised = dict(document)
            revised['content'] = body.content
            revised['approved_hash'] = None
            revised['status'] = 'AWAITING_SCENE_PLAN_APPROVAL'
            if existing:
                from shutil import copy2
                from uuid import uuid4
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
                backup = project.folder / 'history' / ('scene_revision_' + stamp)
                backup.mkdir(parents=True, exist_ok=False)
                copy2(project.folder / SCENE_NAME, backup / 'previous_agent_scene_plan.json')
                for file in existing:
                    copy2(file, backup / file.name)
                # All snapshots succeed before any active artifact is invalidated.
                for file in existing:
                    if file.name == 'storyboard.json':
                        # ProjectManager.load_project requires storyboard.json even
                        # before any assets have been compiled.
                        original = read_json(file)
                        atomic_write_json(file, {**original, 'scenes': []})
                    else:
                        file.unlink()
                atomic_write_json(project.folder / 'script_revision_pending.json', {
                    'revision': stamp, 'archived_files': [p.name for p in existing],
                    'message': 'Scene Plan changed; regenerate dependent assets/plans and render again.'})
            atomic_write_json(project.folder / SCENE_NAME, revised)
            return revised
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.post('/api/projects/{project_id}/scene-plan/approve')
    def approve_scene_plan_route(project_id: str):
        project = _get_project(project_id)
        try:
            return approve_scene_plan(project)
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.post('/api/projects/{project_id}/scene-plan/export')
    def export_scene_plan_route(project_id: str, body: ExportSceneRequest):
        project = _get_project(project_id)
        try:
            return export_scene_brief(project, replace=body.replace)
        except ValidationError as exc:
            raise _safe_script_error(exc) from exc

    @app.get('/api/projects/{project_id}/asset-prompts')
    def get_asset_prompts(project_id: str):
        try:
            return make_prompt_pack(_get_project(project_id))
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/projects/{project_id}/asset-prompts.md')
    def download_asset_prompts(project_id: str):
        try:
            pack = make_prompt_pack(_get_project(project_id))
            return PlainTextResponse(render_prompt_markdown(pack), media_type='text/markdown',
                headers={'Content-Disposition': 'attachment; filename="asset_prompts.md"'})
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/generate', status_code=201)
    def generate_asset_route(project_id: str, body: GenerateAssetRequest):
        project = _get_project(project_id)
        if not body.confirmed:
            raise HTTPException(400, 'Explicit consent for this paid generation request is required')
        if body.kind == 'video':
            raise HTTPException(422, 'OpenAI Video API discontinued; copy video prompt to an external tool')
        try:
            pack = make_prompt_pack(project)
            scene = next((s for s in pack['scenes'] if s['scene_id'] == body.scene_id), None)
            if not scene:
                raise HTTPException(404, 'Scene ID not in Script')
            prompt = _asset_prompt(scene, body.kind)
            # Local per-project/day ceiling protects against accidental repeated billable calls.
            if jobs.active(project_id):
                raise HTTPException(409, 'Another operation is running for this project')
            choice=load_choices(projects_dir)[body.kind]
            key = _provider_key(projects_dir, choice['provider'])
            voice = _chosen_voice(project, body.kind, body.voice, choice['provider'])
            try:
                reserve_api_attempt(project, body.kind)
            except ValueError as exc:
                raise HTTPException(429, str(exc)) from exc
            data, extension, model = _call_asset_provider(project, body.kind, prompt, choice, key, voice,
                                                          scene.get('audio_instructions'))
            return save_generated_asset(project, body.scene_id, body.kind, data, extension, model,
                                        source_prompt=prompt,provider=choice['provider'])
        except (ValidationError, ValueError, TypeError, KeyError, OSError) as exc:
            raise HTTPException(422, 'Asset generation failed: ' + str(exc)[:250]) from exc


    @app.get('/api/projects/{project_id}/assets/status')
    def asset_status(project_id: str):
        project = _get_project(project_id)
        manifest_file = project.folder / 'agent_manifest.json'
        manifest = load_manifest(project) if manifest_file.is_file() else None
        try:
            return {'generated_assets': candidates(project), 'manifest': manifest,
                    'missing': missing_assets(project, manifest) if manifest else [],
                    'api_usage': api_usage(project), 'active_job': jobs.active(project_id)}
        except (ValidationError, ValueError, OSError, TypeError, KeyError) as exc:
            raise HTTPException(422, 'Invalid local asset configuration: ' + str(exc)[:200]) from exc

    @app.put('/api/projects/{project_id}/assets/limits')
    def update_local_limits(project_id: str, body: ApiLimitsRequest):
        project = _get_project(project_id)
        try:
            return set_api_limits(project, image=body.image, audio=body.audio)
        except (ValueError, OSError, TypeError) as exc:
            raise HTTPException(422, str(exc)[:200]) from exc

    @app.post('/api/projects/{project_id}/assets/manifest')
    def ensure_manifest_route(project_id: str):
        project = _get_project(project_id)
        try:
            return prepare_manifest(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/upload', status_code=201)
    async def upload_asset_route(project_id: str, scene_id: str = Form(...),
                                 kind: str = Form(...), file: UploadFile = File(...)):
        project = _get_project(project_id)
        # Upload is user-triggered and local; never trust a supplied filename or path.
        if kind not in ('image', 'video', 'audio'):
            raise HTTPException(422, 'Kind must be image, video or audio')
        if not re.fullmatch(r'[A-Za-z0-9_]+', scene_id):
            raise HTTPException(422, 'Invalid scene ID')
        from .workflow import prepare_manifest
        manifest = prepare_manifest(project)
        scene = next((s for s in manifest['scenes'] if s['scene_id'] == scene_id), None)
        if not scene:
            raise HTTPException(404, 'Scene not found in manifest')
        if kind != 'audio' and ((kind == 'image' and scene['type'] != 'image') or
                                (kind == 'video' and scene['type'] != 'video_clip')):
            raise HTTPException(422, 'Media kind does not match approved scene type')
        suffix = Path(file.filename or '').suffix.lower()
        allowed = {'image': ('.png','.jpg','.jpeg'), 'video': ('.mp4','.mov','.mkv'),
                   'audio': ('.mp3','.wav')}
        if suffix not in allowed[kind]:
            raise HTTPException(422, 'Unsupported file extension')
        payload = await file.read(201 * 1024 * 1024)
        if not payload or len(payload) > 200 * 1024 * 1024:
            raise HTTPException(413, 'Upload must be 1 byte to 200 MB')
        if kind == 'image' and not (payload.startswith(b'\x89PNG\r\n\x1a\n') or
                                     payload.startswith(b'\xff\xd8\xff')):
            raise HTTPException(422, 'Image header does not match PNG/JPEG')
        directory = project.folder / 'assets' / {'image':'images','video':'videos','audio':'audio'}[kind]
        directory.mkdir(parents=True, exist_ok=True)
        name = f'uploaded_{kind}_{scene_id}_{hashlib.sha256(payload).hexdigest()[:12]}{suffix}'
        target = directory / name
        existed_before = target.exists()
        if not existed_before:
            with target.open('xb') as handle:
                handle.write(payload)
        relative = target.relative_to(project.folder).as_posix()
        try:
            result = bind_asset(project, scene_id, 'voice' if kind == 'audio' else 'media', relative)
        except (ValidationError, OSError, ValueError) as exc:
            if not existed_before:
                target.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
        return {'scene_id': scene_id, 'file': relative, 'manifest_status': result['status'],
                'missing': missing_assets(project, result)}

    @app.post('/api/projects/{project_id}/assets/overlays')
    def generate_overlays_route(project_id: str, body: OverlayRequest):
        """Render text overlay cards locally (no provider call) and bind them to the manifest."""
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Wait for the current project job to finish')
        try:
            from ..overlay import generate_overlays
            return generate_overlays(project, scene_ids=body.scene_ids, bind=body.bind)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/auto-bind')
    def auto_bind_route(project_id: str):
        project = _get_project(project_id)
        try:
            return bind_generated(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/bind')
    def manual_bind_route(project_id: str, body: BindAssetRequest):
        project = _get_project(project_id)
        try:
            return bind_asset(project, body.scene_id, body.kind, body.file)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/generate-job', status_code=202)
    def asset_generation_job(project_id: str, body: GenerateAssetRequest):
        project = _get_project(project_id)
        if not body.confirmed:
            raise HTTPException(400, 'Explicit consent required for the paid API request')
        if body.kind == 'video':
            raise HTTPException(422, 'Video API not supported; use external tool and manual import')
        try:
            pack = make_prompt_pack(project)
            scene = next((s for s in pack['scenes'] if s['scene_id'] == body.scene_id), None)
            if not scene:
                raise HTTPException(404, 'Scene ID not found')
            prompt = _asset_prompt(scene, body.kind)
            choice=load_choices(projects_dir)[body.kind]
            key = _provider_key(projects_dir, choice['provider'])
            voice = _chosen_voice(project, body.kind, body.voice, choice['provider'])
            def task(progress):
                progress(f"Đang gọi {choice['provider']} để tạo {body.kind}…")
                data, ext, model = _call_asset_provider(project, body.kind, prompt, choice, key, voice,
                                                        scene.get('audio_instructions'))
                progress('Đang lưu asset và tự động gán vào scene…')
                stored=save_generated_asset(project,body.scene_id,body.kind,data,ext,model,
                                            source_prompt=prompt,provider=choice['provider'])
                try:
                    binding=bind_generated(project)
                    stored['auto_bound']=any(item['file']==stored['file'] for item in binding['bound'])
                    stored['missing_assets']=binding['missing']
                except (ValidationError,OSError,ValueError) as exc:
                    stored['binding_warning']=str(exc)
                return stored
            with submit_lock:
                if jobs.active(project_id):
                    raise HTTPException(409, 'An operation is already running for this project')
                try:
                    reserve_api_attempt(project, body.kind)
                except ValueError as exc:
                    raise HTTPException(429, str(exc)) from exc
                return jobs.submit(project_id, 'generate_' + body.kind, task)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/assets/batch-job',status_code=202)
    def asset_batch_job(project_id: str, body: BatchAssetsRequest):
        project=_get_project(project_id)
        if not body.confirmed:
            raise HTTPException(400,'Explicit confirmation for every selected asset is required')
        if len(set(body.scene_ids))!=len(body.scene_ids):
            raise HTTPException(422,'Duplicate scene IDs')
        try:
            pack=make_prompt_pack(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        mapped={s['scene_id']:s for s in pack['scenes']}
        prompts=[]
        for sid in body.scene_ids:
            if sid not in mapped:raise HTTPException(422,'Scene not found: '+sid)
            scene=mapped[sid]
            try:
                prompt = _asset_prompt(scene, body.kind)
            except ValidationError as exc:
                raise HTTPException(422, str(exc)) from exc
            prompts.append((sid,prompt))
        choice=load_choices(projects_dir)[body.kind]
        try:
            key = _provider_key(projects_dir, choice['provider'])
            voice = _chosen_voice(project, body.kind, body.voice, choice['provider'])
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Reserve all before queueing; partial reservations and hidden extra API calls are forbidden.
        with submit_lock:
            if jobs.active(project_id):raise HTTPException(409,'Project already has an active job')
            from .limits import usage as limit_usage
            try:
                q=limit_usage(project)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise HTTPException(422, 'Invalid API limits configuration') from exc
            if len(prompts)>q['remaining'][body.kind]:
                raise HTTPException(429,f"Only {q['remaining'][body.kind]} {body.kind} attempts remaining today; {len(prompts)} selected")
            # Reserve once per selection, then run sequentially in one tracked background job.
            for sid,prompt in prompts:reserve_api_attempt(project,body.kind)
            def work(progress):
                results=[]
                for i,(sid,prompt) in enumerate(prompts,1):
                    progress(f'{i}/{len(prompts)} · Scene {sid} · {choice["provider"]} ({body.kind})')
                    try:
                        data,ext,model=_call_asset_provider(project,body.kind,prompt,choice,key,voice,
                                                            mapped[sid].get('audio_instructions'))
                        result=save_generated_asset(project,sid,body.kind,data,ext,model,
                            source_prompt=prompt,provider=choice['provider'])
                        results.append({'scene_id':sid,'status':'succeeded','file':result['file']})
                    except Exception as exc:
                        # Continue to next selected scene; results include per-scene errors.
                        results.append({'scene_id':sid,'status':'failed','error':str(exc)[:280]})
                binding=bind_generated(project)
                return {'results':results,'bound':binding['bound'],'missing':binding['missing']}
            return jobs.submit(project_id,'batch_'+body.kind,work)

    @app.get('/api/projects/{project_id}/jobs/{job_id}')
    def get_job_route(project_id: str, job_id: str):
        _get_project(project_id)
        status = jobs.get(project_id, job_id)
        if not status:
            raise HTTPException(404, 'Job not found or expired after server restart')
        return status

    @app.post('/api/projects/{project_id}/render/prepare')
    def render_prepare_route(project_id: str, body: RenderPreparationRequest):
        project = _get_project(project_id)
        if jobs.active(project_id):
            raise HTTPException(409, 'Project has an active job')
        try:
            return prepare_render(project, config_path=default_config, approve=body.approve_manifest,
                                  replace_stale_plan=body.replace_stale_plan)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/render/approve-plan')
    def render_approve_plan_route(project_id: str):
        project = _get_project(project_id)
        try:
            return approve_plan(project)
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/projects/{project_id}/render/start', status_code=202)
    def start_render_route(project_id: str, body: RenderRequest):
        project = _get_project(project_id)
        if not body.confirmed:
            raise HTTPException(400, 'Explicit render confirmation required')
        try:
            from ..planner import validate_editing_plan
            plan = validate_editing_plan(project, read_json(project.folder / 'editing_plan.json'))
            if not plan['plan_meta']['approved_by_user']:
                raise ValidationError('Approve the Editing Plan before rendering')
            manifest = load_manifest(project)
            if missing_assets(project, manifest):
                raise ValidationError('Asset files missing; rebind/validate before rendering')
            if manifest['status'] != 'COMPILED':
                raise ValidationError('Compile the storyboard before rendering')
        except (ValidationError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return jobs.submit(project_id, 'render', lambda progress:
                               render_cli(project_id, projects_dir, default_config, progress,
                                          timeout_minutes=body.timeout_minutes))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/projects/{project_id}/render/output')
    def render_output_route(project_id: str):
        project = _get_project(project_id)
        path = project.folder / 'output' / 'final.mp4'
        if (project.folder / 'script_revision_pending.json').exists():
            raise HTTPException(409, 'Previous video belongs to an older script revision')
        if not path.is_file():
            raise HTTPException(404, 'Output MP4 not found')
        return FileResponse(path, media_type='video/mp4', filename='final.mp4', content_disposition_type='inline')

    public = (static_dir or Path(__file__).parent / 'static').resolve()
    @app.get('/', include_in_schema=False)
    def home():
        index = public / 'index.html'
        if not index.is_file():
            raise HTTPException(503, "GUI build missing: reinstall UI dependencies and verify packaged static assets")
        return FileResponse(index)

    @app.get('/api-settings', include_in_schema=False)
    def api_settings_page():
        return FileResponse(public / 'api-settings.html')

    @app.get('/script-editor', include_in_schema=False)
    def script_editor():
        return FileResponse(public / 'script-editor.html')

    @app.get('/asset-workspace', include_in_schema=False)
    def asset_workspace_page():
        return FileResponse(public / 'asset-workspace.html')

    @app.get('/scene-planner', include_in_schema=False)
    def scene_planner_page():
        return FileResponse(public / 'scene-planner.html')

    @app.get('/render-studio', include_in_schema=False)
    def render_studio_page():
        return FileResponse(public / 'render-studio.html')

    @app.get('/help', include_in_schema=False)
    def help_page():
        return FileResponse(public / 'help.html')

    @app.get('/static/{filename}', include_in_schema=False)
    def common_ui_asset(filename: str):
        # Explicit allowlist; never expose arbitrary files from the package.
        if filename not in {'sidebar.css', 'sidebar.js'}:
            raise HTTPException(404)
        return FileResponse(public / filename,
                            media_type='text/css' if filename.endswith('.css') else 'application/javascript')

    return app
