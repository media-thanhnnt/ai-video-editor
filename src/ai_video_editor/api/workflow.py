"""Shared Core functions used by the GUI. All paths stay inside a Project."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import subprocess
import sys
import threading
from pathlib import Path

from ..agent.manifest import (create_manifest, load_manifest, bind_asset,
                              missing_assets, approve_manifest, compile_storyboard)
from ..agent.asset_workflow import make_prompt_pack
from ..agent.scene_planner import apply_scene_plan
from ..planner import get_default_editing_plan, validate_editing_plan, plan_cache_key
from ..project import ValidationError, atomic_write_json, read_json


def prepare_manifest(project):
    target = project.folder / 'agent_manifest.json'
    if target.exists():
        return load_manifest(project)
    brief_path = project.folder / 'agent_scene_brief.json'
    if not brief_path.exists():
        brief_path = project.folder / 'agent_brief_generated.json'
    if not brief_path.exists():
        # Allow a script draft to drive manual asset preparation without export.
        pack = make_prompt_pack(project)
        brief = {'title': pack['title'], 'scenes': [{
            'scene_id': s['scene_id'], 'type': s['media_type'], 'narration': s['audio_prompt'],
            'prompt': s['image_prompt'] or s['video_prompt']}
            for s in pack['scenes']]}
    else:
        brief = read_json(brief_path)
    return create_manifest(project, brief)


def candidates(project):
    log_file = project.folder / 'generated_assets.json'
    items = []
    if log_file.is_file():
        raw = read_json(log_file)
        if isinstance(raw.get('assets'), list):
            for item in raw['assets']:
                if isinstance(item, dict) and isinstance(item.get('path'), str):
                    items.append(item)
    # Include files that user manually copied into the canonical asset directories.
    seen = {item.get('path') for item in items}
    for sub, kind in (('images', 'image'), ('videos', 'video'), ('audio', 'audio')):
        directory = project.folder / 'assets' / sub
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    relative = path.relative_to(project.folder).as_posix()
                    if relative not in seen:
                        items.append({'path': relative, 'kind': kind, 'scene_id': None,
                                      'provider': 'manual'})
    return items


def bind_generated(project):
    data = prepare_manifest(project)
    # Only match explicitly scene-tagged generated assets; never guess from file names.
    file_list = candidates(project)
    current_prompts = {item['scene_id']: item for item in make_prompt_pack(project)['scenes']}
    completed = []
    for scene in data['scenes']:
        sid, kind = scene['scene_id'], scene['type']
        if kind == 'image_sequence':
            continue
        for field, asset_kind in (('media_file', 'image' if kind == 'image' else 'video'),
                                  ('voice_file', 'audio')):
            if scene.get(field):
                continue
            # After editing a finished script, never auto-bind generated audio/images
            # with stale prompts merely because the scene IDs still match.
            revision_pending = (project.folder / 'script_revision_pending.json').exists()
            source = current_prompts.get(sid, {})
            expected_prompt = (source.get('audio_prompt', '') if field == 'voice_file' else
                               source.get('image_prompt') or source.get('video_prompt') or '')
            expected_hash = hashlib.sha256(expected_prompt.strip().encode('utf-8')).hexdigest()
            match = next((item for item in reversed(file_list)
                          if item.get('scene_id') == sid and item.get('kind') == asset_kind
                          and (not revision_pending or item.get('prompt_sha256') == expected_hash)), None)
            if match:
                try:
                    bind_asset(project, sid, 'voice' if field == 'voice_file' else 'media', match['path'])
                except ValidationError:
                    continue
                completed.append({'scene_id': sid, 'role': field, 'file': match['path']})
    current = load_manifest(project)
    return {'bound': completed, 'missing': missing_assets(project, current), 'manifest': current}


def prepare_render(project, *, config_path: Path, approve: bool = False, use_ai_plan: bool | None = None,
                   replace_stale_plan: bool = False):
    """Never approve editing decisions implicitly: approval is a separate explicit UI click.

    use_ai_plan=None follows config.planning.use_ai: false forces the rule-based plan
    even when an approved AI Scene Plan exists.
    """
    if use_ai_plan is None:
        use_ai_plan = bool(project.config['planning'].get('use_ai', True))
    manifest = load_manifest(project)
    missing = missing_assets(project, manifest)
    if missing:
        raise ValidationError('Missing scene assets: ' + ', '.join(missing))
    if manifest.get('status') not in ('APPROVED', 'COMPILED'):
        if not approve:
            raise ValidationError('Agent manifest must be approved first')
        approve_manifest(project)
    # Rebinding invalidates the previous storyboard. Recompile only on explicit
    # Prepare click, never during passive status/polling. Ensure the old approved
    # editing plan cannot be reused against changed media without confirmation.
    recompiled = load_manifest(project)['status'] != 'COMPILED'
    if recompiled:
        # The storyboard must reflect the new media before validating the old plan.
        # Do not delete the Editing Plan just because a voice/media file was rebound.
        compile_storyboard(project, replace_storyboard=bool(project.storyboard['scenes']))
    from ..project import ProjectManager
    pm = ProjectManager(project.folder.parent, config_path)
    project = pm.load_project(project.project_id)
    path = project.folder / 'editing_plan.json'
    if path.is_file():
        try:
            plan = validate_editing_plan(project, read_json(path), require_current=True)
        except (ValidationError, ValueError, OSError):
            if not replace_stale_plan:
                raise ValidationError('Stale Editing Plan after asset change; confirm regeneration')
        else:
            # A new voice often requires recompiling the storyboard but not
            # changing approved editing decisions. Retain that approval.
            return {'manifest_status': load_manifest(project)['status'],
                    'plan_approved': bool(plan['plan_meta']['approved_by_user']),
                    'source': plan['plan_meta']['source'], 'scenes': len(project.storyboard['scenes'])}

    # Build and validate the replacement in memory BEFORE touching an existing plan.
    # Failure of AI proposal validation cannot destroy the old approved plan.
    if use_ai_plan and (project.folder / 'agent_scene_plan.json').exists():
        from ..agent.scene_planner import build_scene_editing_plan
        candidate = build_scene_editing_plan(project)
    else:
        candidate = get_default_editing_plan(project)
    plan = validate_editing_plan(project, candidate, require_current=True)

    if path.exists():
        from shutil import copy2
        from uuid import uuid4
        copy2(path, project.folder / f'editing_plan_backup_{uuid4().hex[:12]}.json')
    # atomic_write_json uses temporary-file + replace; old plan survives errors
    # during candidate generation or validation.
    atomic_write_json(path, plan)
    return {'manifest_status': load_manifest(project)['status'],
            'plan_approved': bool(plan['plan_meta']['approved_by_user']),
            'source': plan['plan_meta']['source'], 'scenes': len(project.storyboard['scenes'])}


def approve_plan(project):
    path = project.folder / 'editing_plan.json'
    if not path.is_file():
        raise ValidationError('Prepare editing plan first')
    plan = validate_editing_plan(project, read_json(path), require_current=False)
    plan['plan_meta']['cache_key'] = plan_cache_key(project, plan['style'], plan['plan_meta']['seed'])
    plan['plan_meta']['approved_by_user'] = True
    validate_editing_plan(project, plan)
    atomic_write_json(path, plan)
    return {'approved': True, 'source': plan['plan_meta']['source']}


def render_cli(project_id: str, projects_dir: Path, config_path: Path, update,
               *, timeout_minutes: int = 0):
    """Run the existing Compose CLI with checkpoint-based milestone progress.

    Progress is monotonic and stage-based, not frame-accurate. A zero timeout
    disables the outer GUI wall-clock limit (Core still has per-step safeguards).
    """
    import json
    import os
    import time
    from subprocess import TimeoutExpired

    if type(timeout_minutes) is not int or not 0 <= timeout_minutes <= 525600:
        raise ValidationError('Render timeout must be 0..525600 minutes')
    argv = [sys.executable, '-m', 'ai_video_editor.cli', '--projects-dir', str(projects_dir),
            '--default-config', str(config_path), 'compose', project_id, '--deep']
    project_folder = projects_dir / project_id
    checkpoint = project_folder / 'reports' / 'render_checkpoint.json'
    old_checkpoint_mtime = checkpoint.stat().st_mtime_ns if checkpoint.exists() else 0
    storyboard_file = project_folder / 'storyboard.json'
    try:
        storyboard = read_json(storyboard_file)
        scene_total = max(1, len(storyboard.get('scenes', [])))
    except (OSError, ValueError, ValidationError):
        scene_total = 1
    deadline = time.monotonic() + timeout_minutes * 60 if timeout_minutes else None
    update('Đang chuẩn bị render và kiểm tra assets', 1)
    # Keep stdout/stderr in files instead of large buffers in RAM.
    log_dir = project_folder / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'gui_render_last.log'
    kwargs = {'start_new_session': True} if os.name != 'nt' else {
        'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    # The child writes to a file, so without this its stdio would use the ANSI code page
    # (cp932/cp1252 on Windows) while the log is read back as UTF-8.
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    with log_file.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', env=env, **kwargs)
        try:
            last = None
            while proc.poll() is None:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutExpired(argv, timeout_minutes * 60)
                try:
                    if checkpoint.stat().st_mtime_ns <= old_checkpoint_mtime:
                        raise ValueError('Awaiting current run checkpoint')
                    current = json.loads(checkpoint.read_text(encoding='utf-8'))
                    if not isinstance(current, dict):
                        raise ValueError('Invalid checkpoint')
                    scenes_done = len(current.get('scenes', {}))
                    segments_done = len(current.get('segments', {}))
                    stage = current.get('last_step', '')
                    # 5..80: scene encoding; 80..90: segments; 90: video assembled.
                    percent = min(80, 5 + int(75 * scenes_done / scene_total))
                    if scenes_done >= scene_total:
                        percent = min(90, 80 + min(10, segments_done * 2))
                    if stage == 'complete':
                        percent = 92
                    message = (f'Render: {scenes_done}/{scene_total} scene · '
                               f'{segments_done} segments · {stage or "preparing"} · '
                               'Compose / Audio / QA')
                except (OSError, ValueError, TypeError):
                    percent, message = 2, 'Đang kiểm tra và chuẩn bị timeline…'
                state = (percent, message)
                if state != last:
                    update(message, percent)
                    last = state
                time.sleep(1.0)
            if proc.returncode:
                log.flush()
                with log_file.open('r', encoding='utf-8', errors='replace') as reader:
                    tail = reader.read()[-1800:]
                raise ValidationError(tail or f'Render failed (exit {proc.returncode})')
        except BaseException as exc:
            if proc.poll() is None:
                # Terminate the process tree to avoid orphaned FFmpeg children.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                   capture_output=True, check=False)
                else:
                    import signal
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    proc.wait(timeout=8)
                except TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if isinstance(exc, TimeoutExpired):
                raise ValidationError(f'Render exceeded configured {timeout_minutes}-minute timeout') from exc
            raise
    update('Đã hoàn tất dựng video, Audio và QA', 99)
    pending = project_folder / 'script_revision_pending.json'
    if pending.exists():
        pending.unlink()
    return {'log': str(log_file), 'status': 'finished'}
