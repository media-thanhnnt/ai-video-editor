"""Command-line interface for AI Video Editor Phase 1.1."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .project import ProjectManager, ValidationError
from .assets import validate_assets
from .timeline import build_timeline, TimelineError
from .planner import get_default_editing_plan, validate_editing_plan, require_approved_plan, plan_cache_key, PlanError
from .project import read_json, atomic_write_json
from .renderer import render_project, RenderError
from .audio import mux_project_audio, AudioError
from .qa import qa_project
from .ffmpeg_support import check_ffmpeg

# src/ai_video_editor/cli.py -> repository root
ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='AI Video Editor — Phase 1.10 Mixed Media + Audio + Subtitles')
    parser.add_argument('--projects-dir', type=Path, default=ROOT / 'projects')
    parser.add_argument('--default-config', type=Path, default=ROOT / 'config/default_project.json')
    cmd = parser.add_subparsers(dest='action', required=True)
    init = cmd.add_parser('init', help='Create empty project with folder structure')
    init.add_argument('project_id')
    init.add_argument('--title', required=True)
    show = cmd.add_parser('show', help='Print project metadata')
    show.add_argument('project_id')
    validate = cmd.add_parser('validate', help='Validate project JSON and paths (not media presence)')
    validate.add_argument('project_id')
    assets = cmd.add_parser('assets', help='Inspect actual image and voice files; write assets_report.json')
    assets.add_argument('project_id')
    timeline = cmd.add_parser('timeline', help='Build validated frame/sample timeline.json from assets')
    timeline.add_argument('project_id')
    timeline.add_argument('--transition', choices=['none', 'crossfade', 'dissolve'], default='crossfade')
    timeline.add_argument('--length', choices=['short', 'medium', 'long'], default='medium')
    plan = cmd.add_parser('plan', help='Generate deterministic scene-specific editing_plan.json')
    plan.add_argument('project_id')
    plan.add_argument('--style', choices=['documentary', 'calm', 'energetic'])
    plan.add_argument('--seed', type=int)
    timeline.add_argument('--use-plan', action='store_true', help='Apply current editing_plan.json')
    render = cmd.add_parser('render', help='Render video-only MP4 from current editing plan and timeline')
    render.add_argument('project_id')
    render.add_argument('--force', action='store_true', help='Re-render all scenes, segments and final output')
    render.add_argument('--supersample', type=int, choices=[1,2,3,4])
    render.add_argument('--resume', action='store_true', help='Explicitly resume validated checkpoint (default behavior)')
    mux = cmd.add_parser('audio', help='Create final.mp4 with synchronized per-scene voice')
    mux.add_argument('project_id')
    qa = cmd.add_parser('qa', help='Check assets, timeline and rendered media; create integration_report.json')
    qa.add_argument('project_id')
    qa.add_argument('--deep', action='store_true', help='Scan for dark frames and silence (advisory)')
    compose = cmd.add_parser('compose', help='Plan, timeline, render, add audio/subtitles, and QA in one command')
    compose.add_argument('project_id')
    compose.add_argument('--deep', action='store_true')
    compose.add_argument('--force', action='store_true')
    compose.add_argument('--replan', action='store_true', help='Regenerate editing plan explicitly')
    compose.add_argument('--style', choices=['documentary','calm','energetic'])
    compose.add_argument('--seed', type=int)
    approve = cmd.add_parser('approve-plan', help='Explicitly approve existing current editing plan')
    approve.add_argument('project_id')
    agent_init = cmd.add_parser('agent-init', help='Phase 2.1: create manual asset manifest from a structured brief')
    agent_init.add_argument('project_id')
    agent_init.add_argument('--brief', type=Path, required=True)
    agent_init.add_argument('--replace', action='store_true')
    agent_status = cmd.add_parser('agent-status', help='Phase 2.1: show missing assets and plan status')
    agent_status.add_argument('project_id')
    agent_bind = cmd.add_parser('agent-bind', help='Bind a locally supplied media or voice file')
    agent_bind.add_argument('project_id')
    agent_bind.add_argument('--scene', required=True)
    agent_bind.add_argument('--kind', required=True, choices=['media','voice'])
    agent_bind.add_argument('--file', required=True)
    agent_approve = cmd.add_parser('agent-approve', help='Approve agent plan after assets are ready')
    agent_approve.add_argument('project_id')
    agent_compile = cmd.add_parser('agent-compile', help='Compile reviewed plan to Core storyboard.json')
    agent_compile.add_argument('project_id')
    agent_compile.add_argument('--replace-storyboard', action='store_true')
    script_cmd = cmd.add_parser('agent-script', help='Phase 2.2: create reviewable script from OpenAI or manual JSON')
    script_cmd.add_argument('project_id')
    script_input = script_cmd.add_mutually_exclusive_group(required=True)
    script_input.add_argument('--request', help='Natural language video request')
    script_input.add_argument('--request-file', type=Path, help='UTF-8 plain text request file')
    script_input.add_argument('--import-json', type=Path, help='Offline import: JSON with script, scenes and prompts')
    script_cmd.add_argument('--model', default='gpt-4.1-mini')
    script_cmd.add_argument('--replace', action='store_true')
    approve_script_cmd = cmd.add_parser('agent-script-approve', help='Approve Phase 2.2 script draft after review')
    approve_script_cmd.add_argument('project_id')
    export_script_cmd = cmd.add_parser('agent-script-export', help='Export approved script as Phase 2.1 manual brief')
    export_script_cmd.add_argument('project_id')
    export_script_cmd.add_argument('--replace', action='store_true')
    script_status_cmd = cmd.add_parser('agent-script-status', help='Inspect Phase 2.2 draft, status and usage')
    script_status_cmd.add_argument('project_id')
    scene_cmd = cmd.add_parser('agent-scene-plan', help='Phase 2.3: propose an AI editing plan from an approved script')
    scene_cmd.add_argument('project_id')
    scene_src = scene_cmd.add_mutually_exclusive_group(required=True)
    scene_src.add_argument('--model', help='OpenAI model for scene planning')
    scene_src.add_argument('--import-json', type=Path, help='Offline scene plan JSON')
    scene_cmd.add_argument('--replace', action='store_true')
    for name, description in (
        ('agent-scene-status','Inspect scene-plan draft and token usage'),
        ('agent-scene-approve','Approve Phase 2.3 scene proposal'),
        ('agent-scene-export','Export approved scene decisions to a manual-asset brief'),
        ('agent-scene-apply','Compile approved scene decisions to Core editing_plan.json')):
        sp=cmd.add_parser(name, help=description)
        sp.add_argument('project_id')
        if name in ('agent-scene-export','agent-scene-apply'):
            sp.add_argument('--replace',action='store_true')
    cmd.add_parser('agent-tools', help='List safe Phase 2.1 agent tools')
    cmd.add_parser('list', help='List local projects')
    args = parser.parse_args(argv)
    pm = ProjectManager(args.projects_dir, args.default_config)
    try:
        if args.action == 'init':
            project = pm.create_project(args.project_id, args.title)
            print(f'CREATED: {project.folder}')
        elif args.action == 'show':
            project = pm.load_project(args.project_id)
            print(json.dumps({'project_id':project.project_id,'title':project.storyboard['title'],
                              'scenes':len(project.storyboard['scenes']),
                              'status':project.state['status'],'folder':str(project.folder)},
                             ensure_ascii=False, indent=2))
        elif args.action == 'validate':
            project = pm.load_project(args.project_id)
            print(f'VALID: {project.project_id}; {len(project.storyboard["scenes"])} scene(s); structural only')
        elif args.action == 'assets':
            project = pm.load_project(args.project_id)
            report = validate_assets(project)
            print(json.dumps({'project_id': project.project_id, 'status': report['status'],
                              'scenes': report['scene_count'],
                              'report': str(project.folder / 'reports' / 'assets_report.json')},
                             ensure_ascii=False, indent=2))
            if report['status'] != 'PASS':
                return 1
        elif args.action == 'timeline':
            project = pm.load_project(args.project_id)
            editing_plan = None
            if args.use_plan:
                editing_plan = validate_editing_plan(project, read_json(project.folder / 'editing_plan.json'))
            result = build_timeline(project, transition=args.transition,
                                    transition_length=args.length, editing_plan=editing_plan)
            print(json.dumps({'project_id': project.project_id, 'status': 'PASS',
                              'scenes': len(result['video_track']),
                              'total_frames': result['total_frames'],
                              'total_samples': result['total_samples'],
                              'duration_seconds': result['total_duration_seconds'],
                              'warnings': result['warnings'],
                              'file': str(project.folder / 'timeline.json')}, ensure_ascii=False, indent=2))
        elif args.action == 'plan':
            project = pm.load_project(args.project_id)
            result = get_default_editing_plan(project, style=args.style, seed=args.seed)
            print(json.dumps({'project_id': project.project_id, 'status': 'PASS',
                              'source': result['plan_meta']['source'],
                              'style': result['style'], 'seed': result['plan_meta']['seed'],
                              'scenes': len(result['scenes']),
                              'cache_key': result['plan_meta']['cache_key'],
                              'file': str(project.folder / 'editing_plan.json')},
                             ensure_ascii=False, indent=2))
        elif args.action == 'render':
            project = pm.load_project(args.project_id)
            result = render_project(project, force=args.force, supersample_factor=args.supersample)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == 'audio':
            project = pm.load_project(args.project_id)
            result = mux_project_audio(project)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == 'qa':
            project = pm.load_project(args.project_id)
            result = qa_project(project, deep=args.deep)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result['status'] != 'PASS':
                return 1
        elif args.action == 'approve-plan':
            project = pm.load_project(args.project_id)
            path = project.folder/'editing_plan.json'
            # Explicit user review may reapprove an old plan without losing curated scene choices.
            plan = validate_editing_plan(project,read_json(path),require_current=False)
            plan['plan_meta']['cache_key']=plan_cache_key(project,plan['style'],plan['plan_meta']['seed'])
            plan['plan_meta']['approved_by_user']=True
            atomic_write_json(path,plan)
            print(f'APPROVED: {args.project_id}; style={plan["style"]}; seed={plan["plan_meta"]["seed"]}')
        elif args.action == 'compose':
            project = pm.load_project(args.project_id)
            check_ffmpeg(project)
            assets_report = validate_assets(project)
            if assets_report['status'] != 'PASS':
                raise ValidationError('Assets invalid; see assets_report.json')
            plan_path = project.folder / 'editing_plan.json'
            plan = None
            if plan_path.is_file() and not args.replan:
                try:
                    saved = validate_editing_plan(project, read_json(plan_path),require_current=True)
                except (ValidationError, OSError, ValueError):
                    saved = None
                    # A previously approved plan must never be silently discarded.
                    # The user may reapprove its choices or request --replan explicitly.
                    if args.style is None and args.seed is None:
                        try:
                            old_plan = validate_editing_plan(project, read_json(plan_path),
                                                             require_current=False)
                        except (ValidationError, OSError, ValueError):
                            old_plan = None
                        if old_plan is not None and old_plan['plan_meta']['approved_by_user']:
                            raise PlanError('Approved editing plan needs reapproval after a change; '
                                            'run ai-video-editor approve-plan PROJECT or compose --replan')
                if saved is not None:
                    if args.style is None and args.seed is None:
                        plan = saved
                    elif ((args.style is None or args.style == saved['style']) and
                          (args.seed is None or args.seed == saved['plan_meta']['seed'])):
                        plan = saved
            if plan is None:
                plan = get_default_editing_plan(project,style=args.style,seed=args.seed)
            require_approved_plan(project,plan)
            timeline = build_timeline(project, asset_report=assets_report, editing_plan=plan)
            rendered = render_project(project, force=args.force)
            finished = mux_project_audio(project)
            qa = qa_project(project, deep=args.deep)
            print(json.dumps({'phase':'1.10.3','status':qa['status'], 'project_id':project.project_id,
                              'frames':timeline['total_frames'], 'scenes':len(plan['scenes']),
                              'output':finished['output'],'render_cache_scenes':rendered['cached_scenes'],
                              'checks':qa['checks'], 'warnings':qa['warnings']},ensure_ascii=False,indent=2))
            if qa['status'] != 'PASS':
                return 1
        elif args.action in ('agent-scene-plan','agent-scene-status','agent-scene-approve',
                             'agent-scene-export','agent-scene-apply'):
            from .agent.scene_planner import (
                generate_openai_scene_plan, create_scene_plan, load_scene_plan,
                approve_scene_plan, export_scene_brief, apply_scene_plan)
            from .agent.script_generator import load_script_draft
            project=pm.load_project(args.project_id)
            if args.action=='agent-scene-plan':
                # Validate existing draft before a potentially billable API request.
                if (project.folder/'agent_scene_plan.json').exists() and not args.replace:
                    raise ValidationError('Scene plan exists; use --replace to overwrite')
                if args.import_json:
                    content=read_json(args.import_json)
                    payload=create_scene_plan(project,content,source='manual',replace=args.replace)
                else:
                    script=load_script_draft(project)
                    from .agent.script_generator import digest
                    if script.get('status')!='SCRIPT_APPROVED' or script.get('approved_hash')!=digest(script['content']):
                        raise ValidationError('Approve unchanged Phase 2.2 script before AI planning')
                    content,usage=generate_openai_scene_plan(script['content'],model=args.model)
                    payload=create_scene_plan(project,content,source='openai',usage=usage,replace=args.replace)
            elif args.action=='agent-scene-approve':
                payload=approve_scene_plan(project)
            elif args.action=='agent-scene-export':
                payload=export_scene_brief(project,replace=args.replace)
            elif args.action=='agent-scene-apply':
                payload=apply_scene_plan(project,replace=args.replace)
            else:
                doc=load_scene_plan(project)
                payload={'project_id':project.project_id,'status':doc['status'],
                         'scenes':len(doc['content']['scenes']),'source':doc['source'],
                         'usage':doc['usage']}
            print(json.dumps(payload,ensure_ascii=False,indent=2))
        elif args.action in ('agent-script', 'agent-script-approve', 'agent-script-export', 'agent-script-status'):
            from .agent.script_generator import (openai_generate, create_script_draft, load_script_draft,
                                                 approve_script, export_agent_brief)
            project = pm.load_project(args.project_id)
            if args.action == 'agent-script':
                # Reject before billable API call when overwriting was not authorized.
                if (project.folder / 'agent_script_draft.json').exists() and not args.replace:
                    raise ValidationError('Agent script draft exists; use --replace to overwrite')
                if args.import_json:
                    content = read_json(args.import_json)
                    payload = create_script_draft(project, content, source='manual', replace=args.replace)
                else:
                    requirement = (args.request_file.read_text(encoding='utf-8') if args.request_file else args.request)
                    if not requirement or not requirement.strip():
                        raise ValidationError('Natural language requirement must not be empty')
                    content, usage = openai_generate(requirement, model=args.model)
                    payload = create_script_draft(project, content, source='openai', brief=requirement,
                                                  usage=usage, replace=args.replace)
            elif args.action == 'agent-script-approve':
                payload = approve_script(project)
            elif args.action == 'agent-script-export':
                payload = export_agent_brief(project, replace=args.replace)
            else:
                draft = load_script_draft(project)
                payload = {'project_id': project.project_id, 'status': draft['status'],
                           'source': draft['source'], 'title': draft['content']['title'],
                           'scenes': len(draft['content']['scenes']), 'usage': draft['usage']}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.action in ('agent-init','agent-status','agent-bind','agent-approve','agent-compile','agent-tools'):
            from .agent import (registry, create_manifest, load_manifest, missing_assets,
                                bind_asset, approve_manifest, compile_storyboard)
            if args.action == 'agent-tools':
                payload=registry().describe()
            else:
                project=pm.load_project(args.project_id)
                if args.action == 'agent-init':
                    payload=create_manifest(project,read_json(args.brief),replace=args.replace)
                elif args.action == 'agent-status':
                    manifest=load_manifest(project)
                    payload={'project_id':project.project_id,'status':manifest['status'],
                             'missing_assets':missing_assets(project,manifest),'scenes':len(manifest['scenes'])}
                elif args.action == 'agent-bind':
                    payload=bind_asset(project,args.scene,args.kind,args.file)
                elif args.action == 'agent-approve':
                    payload=approve_manifest(project)
                else:
                    payload=compile_storyboard(project,replace_storyboard=args.replace_storyboard)
            print(json.dumps(payload,ensure_ascii=False,indent=2))
        elif args.action == 'list':
            for pid in pm.list_projects():
                print(pid)
    except ValidationError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
