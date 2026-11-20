"""Phase 1.8: non-destructive end-to-end artifact validation.

Default QA checks real assets, plan/timeline freshness and both MP4 outputs.
Optional --deep scans for suspicious black video and silence during expected speech.
Those content heuristics are advisory; naturally dark scenes and quiet voices are allowed.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

from .assets import validate_assets
from .audio import inspect_final
from .planner import validate_editing_plan
from .project import Project, ValidationError, atomic_write_json, read_json
from .renderer import verify_video, verify_render_checkpoint
from .timeline import build_timeline, validate_timeline


class QAError(ValidationError):
    pass


def _exec(cmd: list[str], timeout: int = 900) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QAError(f'QA could not start {cmd[0]}: {exc}') from exc
    if proc.returncode:
        raise QAError(f'QA command {cmd[0]} returned {proc.returncode}: {proc.stderr[-800:]}')
    return proc.stderr


def _intervals(text: str, kind: str) -> list[tuple[float, float]]:
    """Parse ffmpeg's blackdetect/silencedetect diagnostics (end positions)."""
    if kind == 'black':
        pattern = r'black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)'
    else:
        pattern = r'silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)'
    ranges = []
    for match in re.finditer(pattern, text):
        a, b = map(float, match.groups())
        ranges.append((a, b) if kind == 'black' else (max(0, a-b), a))
    return ranges


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _content_scan(path: Path, timeline: dict[str, Any]) -> dict[str, Any]:
    """Read-only decode. Heuristics never fail the project automatically."""
    warnings = []
    black_log = _exec(['ffmpeg','-hide_banner','-nostdin','-v','info','-i',str(path),
        '-map','0:v:0','-vf','blackdetect=d=1.0:pix_th=0.04:pic_th=0.98',
        '-an','-f','null','-'])
    black = _intervals(black_log, 'black')
    # Because the documentary is intentionally dark, every interval is a warning only.
    for begin, end in black:
        warnings.append({'code':'POSSIBLE_BLACK_VIDEO', 'start_seconds':round(begin,3),
                         'end_seconds':round(end,3),
                         'message':'Review footage: very dark frames may be intentional'})
    silence_log = _exec(['ffmpeg','-hide_banner','-nostdin','-v','info','-i',str(path),
        '-map','0:a:0','-af','silencedetect=noise=-50dB:d=0.65',
        '-vn','-f','null','-'])
    silence = _intervals(silence_log, 'silence')
    sr = int(timeline['time_base']['audio_sample_rate'])
    voices = [(v['start_sample']/sr, v['end_sample']/sr) for v in timeline['voice_track']]
    unexpected = []
    for interval in silence:
        # A large silent span within expected narration may mean missing voice.
        intersection = max((_overlap(interval, v) for v in voices), default=0.0)
        if intersection >= 0.65:
            unexpected.append(interval)
            warnings.append({'code':'SILENCE_DURING_VOICE',
                'start_seconds':round(interval[0],3),'end_seconds':round(interval[1],3),
                'message':'Check narration; a long quiet pause may be intentional'})
    return {'black_intervals':black, 'silence_intervals':silence,
            'silence_during_voice':unexpected, 'warnings':warnings}


def qa_project(project: Project, *, deep: bool = False, write_report: bool = True) -> dict[str, Any]:
    """Check project without touching editing plan, timeline or rendered media.

    The report is always written (when requested), including failure diagnostics.
    A failing check does not prevent independent checks from running.
    """
    report: dict[str, Any] = {
        'schema_version':'1.0', 'phase':'1.8', 'project_id':project.project_id,
        'generated_utc':datetime.now(timezone.utc).isoformat(), 'mode':'deep' if deep else 'standard',
        'status':'FAIL', 'checks':{}, 'warnings':[], 'summary':{},
    }
    checks = report['checks']
    def check(name: str, func):
        try:
            detail = func()
            checks[name] = {'status':'PASS', 'details':detail}
            return detail
        except (Exception) as exc:
            checks[name] = {'status':'FAIL','error':f'{type(exc).__name__}: {exc}'}
            return None

    assets = check('assets', lambda: _checked_assets(project))
    plan = check('editing_plan', lambda: validate_editing_plan(project,read_json(project.folder/'editing_plan.json')))
    timeline = check('timeline', lambda: _checked_timeline(project, assets, plan)) if assets is not None and plan is not None else None
    if timeline is None and (assets is None or plan is None):
        checks['timeline']={'status':'BLOCKED','error':'Assets or editing_plan failed validation'}
    if timeline is not None:
        expected = int(timeline['total_frames'])
        width,height = (int(timeline['resolution'][k]) for k in ('width','height'))
        fps = Fraction(timeline['time_base']['fps'])
        check('video_only', lambda: (verify_render_checkpoint(project,plan,timeline,project.folder/'output'/'video_only.mp4'), verify_video(project.folder/'output'/'video_only.mp4', expected,width,height,fps,final=True))[1])
        check('final_video_audio', lambda: inspect_final(project.folder/'output'/'final.mp4',timeline,project.config))
        if getattr(project, 'storyboard', {}).get('subtitle_file') or getattr(project, 'storyboard', {}).get('subtitles'):
            def _check_subtitle_output():
                srt = project.folder/'output'/'subtitles.srt'
                if not srt.is_file() or '-->' not in srt.read_text(encoding='utf-8-sig'):
                    raise QAError('Generated subtitle SRT missing or empty; rerun audio')
                return {'file':str(srt),'mode':project.config['subtitles']['mode']}
            check('subtitles',_check_subtitle_output)
        if deep and checks['final_video_audio']['status']=='PASS':
            scan = check('content_scan',lambda: _content_scan(project.folder/'output'/'final.mp4',timeline))
            if scan:
                report['warnings'].extend(scan['warnings'])
        report['warnings'].extend(timeline.get('warnings',[]))
        report['summary']={'scenes':len(timeline['video_track']), 'total_frames':expected,
            'duration_seconds':float(timeline['total_duration_seconds']),
            'resolution':f'{width}x{height}','fps':str(fps),
            'audio_sample_rate':int(timeline['time_base']['audio_sample_rate']),
            'audio_samples':int(timeline['total_samples'])}
    else:
        checks['video_only']={'status':'BLOCKED','error':'Timeline validation did not pass'}
        checks['final_video_audio']={'status':'BLOCKED','error':'Timeline validation did not pass'}
        if deep:
            checks['content_scan']={'status':'BLOCKED','error':'Final media not validated'}
    report['status']='PASS' if all(c['status']=='PASS' for c in checks.values()) else 'FAIL'
    # Do not store huge editing plans inside report; keep only summarized check details.
    if checks['editing_plan']['status']=='PASS':
        checks['editing_plan']['details']={'scenes':len(plan['scenes']), 'style':plan.get('style')}
    if checks['timeline']['status']=='PASS':
        checks['timeline']['details']={'frames':int(timeline['total_frames']),
            'samples':int(timeline['total_samples']), 'fresh':True}
    if checks['assets']['status']=='PASS':
        checks['assets']['details']={'scenes':assets['scene_count'], 'status':assets['status']}
    if write_report:
        atomic_write_json(project.folder/'reports'/'integration_report.json',report)
    return report


def _checked_assets(project: Project) -> dict[str, Any]:
    # No unnecessary rewrite of original Phase 1.2 asset report.
    report = validate_assets(project,write_report=False)
    if report['status']!='PASS':
        failed=[x['scene_id'] for x in report['scene_results'] if x['status']!='PASS']
        raise QAError(f'Assets invalid: scenes={failed}; free disk space may also be insufficient')
    return report


def _checked_timeline(project: Project, assets: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    saved = read_json(project.folder/'timeline.json')
    validate_timeline(saved)
    current=build_timeline(project,asset_report=assets,editing_plan=plan,write_output=False)
    for key in ('time_base','resolution','video_track','voice_track','total_frames','total_samples',
                'planning_cache_key','input_hashes'):
        if current[key]!=saved.get(key):
            raise QAError(f'Stale timeline ({key}). Rebuild timeline and render/audio as needed')
    return saved
