"""Phase 1.3 — deterministic, sample-aware visual/audio timeline; no rendering or AI.

Scene duration and overlap use integer frames. Voice duration is actual decoded PCM
sample count at the project sample rate. All output timestamps are derived values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from typing import Any

from .assets import validate_assets
from .project import Project, ValidationError, atomic_write_json


class TimelineError(ValidationError):
    """Invalid transition, timing, or media timing evidence."""


def round_fraction(value: Fraction) -> int:
    """Round nonnegative rational to nearest integer, ties upward."""
    if value < 0:
        raise TimelineError('Negative timing is unsupported')
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def seconds_to_frames(seconds: int | float | str, fps: Fraction) -> int:
    if isinstance(seconds, bool):
        raise TimelineError('Invalid seconds value')
    try:
        value = Fraction(str(seconds))
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise TimelineError(f'Invalid seconds value: {seconds!r}') from exc
    if value < 0:
        raise TimelineError('Negative duration not supported')
    return round_fraction(value * fps)


def samples_to_frames_ceil(samples: int, fps: Fraction, sample_rate: int) -> int:
    if type(samples) is not int or samples < 0 or sample_rate <= 0:
        raise TimelineError('Invalid sample count or rate')
    value = Fraction(samples) * fps / sample_rate
    return -(-value.numerator // value.denominator)


def frame_to_sample(frame: int, fps: Fraction, sample_rate: int) -> int:
    if type(frame) is not int or frame < 0:
        raise TimelineError('Frame must be nonnegative integer')
    return round_fraction(Fraction(frame * sample_rate, 1) / fps)


def seconds_from_frames(frames: int, fps: Fraction) -> float:
    return round(float(Fraction(frames, 1) / fps), 9)


def _enforce_transition_choice(transition: str, length: str | None) -> None:
    if transition not in ('none', 'crossfade', 'dissolve'):
        raise TimelineError(f'Unknown transition: {transition!r}')
    if transition == 'none':
        if length is not None:
            raise TimelineError('transition_length must be null for none')
    elif length not in ('short', 'medium', 'long'):
        raise TimelineError('transition_length must be short, medium or long')


def _transition_overlaps(durations: list[int], heads: list[int], tails: list[int],
                         wanted: list[int], min_hold: int, policy: str) -> tuple[list[int], list[dict[str, Any]]]:
    """Deterministic left-to-right allocation; preserve at least min_hold frames per scene.

    O[i] <= T[i]+H[i+1] and O[i-1]+O[i] <= D[i]-min_hold.
    """
    if len(wanted) != len(durations) - 1:
        raise TimelineError('Invalid transition count')
    overlaps: list[int] = []
    warnings: list[dict[str, Any]] = []
    for i, desired in enumerate(wanted):
        left_capacity = durations[i] - min_hold - (overlaps[i-1] if i else 0)
        right_capacity = durations[i+1] - min_hold
        allowed = max(0, min(tails[i] + heads[i+1], left_capacity, right_capacity))
        actual = min(desired, allowed)
        if actual != desired:
            if policy == 'fail':
                raise TimelineError(f'Transition {i+1}->{i+2} requests {desired} frames but max is {allowed}')
            warnings.append({'code': 'TRANSITION_CLAMPED', 'boundary_after_index': i+1,
                             'requested_frames': desired, 'actual_frames': actual})
        overlaps.append(actual)
    return overlaps, warnings


def _validate_audio_metadata(report: dict[str, Any], project: Project) -> list[int]:
    if report.get('status') != 'PASS':
        bad = [s['scene_id'] for s in report.get('scene_results', []) if s['status'] != 'PASS']
        raise TimelineError(f'Asset validation failed; invalid scenes: {bad}')
    sample_rate = project.config['output']['audio_sample_rate']
    lengths = []
    for scene in project.storyboard['scenes']:
        if scene.get('type') in ('image_sequence','video_clip') and not scene.get('audio'):
            lengths.append(0)
            continue
        info = report['file_results'][scene['audio']]['info']
        samples = info.get('decoded_samples')
        if type(samples) is not int or samples <= 0 or info.get('timeline_sample_rate') != sample_rate:
            raise TimelineError(f'Scene {scene["scene_id"]} missing decoded sample evidence')
        lengths.append(samples)
    return lengths


def _timeline_asset_keys(project: Project) -> set[str]:
    """Only scene image/video/voice assets influence timeline freshness."""
    from .assets import sequence_frame_paths
    keys: set[str] = set()
    for scene in project.storyboard['scenes']:
        kind = scene.get('type', 'image')
        if kind == 'image':
            keys.add(scene['image'])
        elif kind == 'video_clip':
            keys.add(scene['video'])
        else:
            keys.update(sequence_frame_paths(scene))
        if scene.get('audio'):
            keys.add(scene['audio'])
    return keys


def build_timeline(project: Project, *, asset_report: dict[str, Any] | None = None,
                   transition: str = 'crossfade', transition_length: str = 'medium',
                   min_hold_frames: int = 1, write_output: bool = True,
                   editing_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate source assets, build and atomically save the timeline.

    Phase 1.3 uses a provisional common transition for every adjacent scene. The
    scene-specific choices of the Phase 1.4 planner replace this in a later phase.
    """
    _enforce_transition_choice(transition, None if transition == 'none' else transition_length)
    if editing_plan is not None:
        from .planner import validate_editing_plan
        validate_editing_plan(project, editing_plan)
    if type(min_hold_frames) is not int or min_hold_frames < 1:
        raise TimelineError('min_hold_frames must be positive integer')
    scenes = project.storyboard['scenes']
    if not scenes:
        raise TimelineError('Cannot build a timeline for an empty storyboard')
    cfg = project.config
    fps = Fraction(cfg['output']['fps'])
    sample_rate = cfg['output']['audio_sample_rate']
    report = asset_report if asset_report is not None else validate_assets(project, write_report=True)
    voice_samples = _validate_audio_metadata(report, project)

    head = seconds_to_frames(cfg['timing']['pad_head_seconds'], fps)
    tail = seconds_to_frames(cfg['timing']['pad_tail_seconds'], fps)
    min_scene = max(1, seconds_to_frames(cfg['timing']['min_scene_seconds'], fps))
    heads = [head if samples > 0 else 0 for samples in voice_samples]
    tails: list[int] = []
    durations: list[int] = []
    voice_frames: list[int] = []
    for scene, samples in zip(scenes, voice_samples):
        frames = samples_to_frames_ceil(samples, fps, sample_rate)
        scene_head = head if samples > 0 else 0
        scene_tail = tail if samples > 0 else 0
        if scene.get('type') == 'video_clip':
            duration = report['file_results'][scene['video']]['info']['target_frames']
            if duration < scene_head + frames + scene_tail:
                raise TimelineError(f'Scene {scene["scene_id"]}: narration plus padding exceeds video clip length')
            actual_tail = duration - scene_head - frames
        elif scene.get('type') == 'image_sequence':
            seq = scene['sequence']
            if Fraction(seq['fps']) != fps:
                raise TimelineError(f'Scene {scene["scene_id"]}: sequence fps must match output fps ({fps})')
            duration = seq['frame_count']  # Exactly 90 frames = 3 seconds at 30fps.
            if duration < scene_head + frames + scene_tail:
                raise TimelineError(f'Scene {scene["scene_id"]}: voice + head/tail padding exceed sequence frame count')
            actual_tail = duration - scene_head - frames
        else:
            actual_tail = max(tail, min_scene - head - frames)
            duration = head + frames + actual_tail
        voice_frames.append(frames)
        tails.append(actual_tail)
        durations.append(duration)

    lengths = cfg['transitions']['length_seconds']
    if editing_plan is None:
        requested = 0 if transition == 'none' else seconds_to_frames(lengths[transition_length], fps)
        wanted = [requested] * (len(scenes) - 1)
        transition_types = [transition] * (len(scenes) - 1)
    else:
        transition_types = [s['transition_to_next'] for s in editing_plan['scenes'][:-1]]
        wanted = [0 if s['transition_to_next'] == 'none' else
                  seconds_to_frames(lengths[s['transition_length']], fps)
                  for s in editing_plan['scenes'][:-1]]
    overlaps, warnings = _transition_overlaps(durations, heads, tails, wanted,
                                               min_hold_frames, cfg['transitions']['violation_policy'])
    video_track: list[dict[str, Any]] = []
    voice_track: list[dict[str, Any]] = []
    first_frame = 0
    last_voice_end = 0
    for i, (scene, samples, frames) in enumerate(zip(scenes, voice_samples, voice_frames)):
        end_frame = first_frame + durations[i]
        voice_start = frame_to_sample(first_frame + heads[i], fps, sample_rate)
        voice_end = voice_start + samples
        if voice_start < last_voice_end:
            raise TimelineError(f'Voice overlaps at scene {scene["scene_id"]}')
        if voice_end > frame_to_sample(end_frame, fps, sample_rate):
            raise TimelineError(f'Voice exceeds visual scene at {scene["scene_id"]}')
        transition_frames = overlaps[i] if i < len(overlaps) else 0
        video_track.append({
            'scene_id': scene['scene_id'], 'image': scene.get('image'),
            'fit_mode': cfg['image']['fit_mode'],
            'start_frame': first_frame, 'end_frame': end_frame,
            'duration_frames': durations[i], 'head_padding_frames': heads[i],
            'tail_padding_frames': tails[i],
            'transition_out': {'type': (transition_types[i] if transition_frames else 'none')
                               if i < len(transition_types) else 'none',
                               'duration_frames': transition_frames},
            'start_seconds': seconds_from_frames(first_frame, fps),
            'end_seconds': seconds_from_frames(end_frame, fps),
        })
        if scene.get('type') == 'video_clip':
            video_track[-1]['type'] = 'video_clip'
            video_track[-1]['video'] = scene['video']
        if scene.get('type') == 'image_sequence':
            video_track[-1]['type'] = 'image_sequence'
            video_track[-1]['sequence'] = scene['sequence']
        if editing_plan is not None:
            video_track[-1]['motion'] = {
                'type': editing_plan['scenes'][i]['motion'],
                'intensity': editing_plan['scenes'][i]['motion_intensity'],
            }
        if samples:
            voice_track.append({'scene_id': scene['scene_id'], 'source': scene['audio'],
                                'start_sample': voice_start, 'duration_samples': samples,
                                'end_sample': voice_end,
                                'start_seconds': round(voice_start / sample_rate, 9),
                                'end_seconds': round(voice_end / sample_rate, 9)})
        last_voice_end = voice_end
        first_frame = end_frame - transition_frames

    total_frames = video_track[-1]['end_frame']
    total_samples = frame_to_sample(total_frames, fps, sample_rate)
    if last_voice_end > total_samples:
        raise TimelineError('Last voice exceeds final timeline')
    voice_gaps = []
    previous_end = 0
    for voice in voice_track:
        if voice['start_sample'] > previous_end:
            voice_gaps.append([previous_end, voice['start_sample']])
        previous_end = voice['end_sample']
    if total_samples > previous_end:
        voice_gaps.append([previous_end, total_samples])

    intro = seconds_to_frames(cfg['transitions']['intro_outro_seconds'], fps) if cfg['transitions']['intro'] != 'none' else 0
    outro = seconds_to_frames(cfg['transitions']['intro_outro_seconds'], fps) if cfg['transitions']['outro'] != 'none' else 0
    if intro > durations[0]:
        raise TimelineError('Intro fade exceeds first scene')
    if outro > tails[-1]:
        if cfg['transitions']['violation_policy'] == 'fail':
            raise TimelineError('Outro fade exceeds final tail padding')
        warnings.append({'code': 'OUTRO_CLAMPED', 'requested_frames': outro, 'actual_frames': tails[-1]})
        outro = tails[-1]
    if intro > heads[0] and voice_samples[0] > 0:
        warnings.append({'code': 'INTRO_OVER_VOICE', 'message': 'Intro fade may overlap voice; increase head padding if unwanted'})

    result: dict[str, Any] = {
        'schema_version': '1.1', 'project_id': project.project_id, 'phase': '1.3',
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'time_base': {'fps': f'{fps.numerator}/{fps.denominator}', 'audio_sample_rate': sample_rate},
        'resolution': {'width': cfg['output']['width'], 'height': cfg['output']['height']},
        'planning_source': 'rule_based' if editing_plan is not None else 'phase1.3_provisional_rule',
        'planning_cache_key': editing_plan['plan_meta']['cache_key'] if editing_plan is not None else None,
        'video_track': video_track, 'voice_track': voice_track,
        'intro': {'type': cfg['transitions']['intro'], 'duration_frames': intro},
        'outro': {'type': cfg['transitions']['outro'], 'duration_frames': outro},
        'music_track': None, 'subtitles': {'mode': 'off', 'file': None},
        'qa_expectations': {'voice_gap_samples': voice_gaps},
        'total_frames': total_frames, 'total_samples': total_samples,
        'total_duration_seconds': seconds_from_frames(total_frames, fps),
        'approximate_timing': False, 'warnings': warnings,
        'input_hashes': {key: report['input_hashes'][key] for key in _timeline_asset_keys(project)
                         if key in report['input_hashes']},
    }
    validate_timeline(result)
    if write_output:
        atomic_write_json(project.folder / 'timeline.json', result)
    return result


def validate_timeline(timeline: dict[str, Any]) -> None:
    """Validate a Phase 1.3 timeline independently of the builder."""
    try:
        fps = Fraction(timeline['time_base']['fps'])
        sr = timeline['time_base']['audio_sample_rate']
        visual, voices = timeline['video_track'], timeline['voice_track']
        if fps <= 0 or type(sr) is not int or sr <= 0 or not visual or len(visual) < len(voices):
            raise TimelineError('Invalid track structure or timebase')
        if visual[0]['start_frame'] != 0:
            raise TimelineError('First scene must start at frame 0')
        prev_voice_end = 0
        voice_by_id = {v['scene_id']:v for v in voices}
        if len(voice_by_id) != len(voices) or any(sid not in [v['scene_id'] for v in visual] for sid in voice_by_id):
            raise TimelineError('Duplicate or unknown voice IDs')
        for i, scene in enumerate(visual):
            voice = voice_by_id.get(scene['scene_id'])
            if scene.get('type') not in ('image_sequence','video_clip') and voice is None:
                raise TimelineError(f'Image scene {scene["scene_id"]} missing voice')
            if scene['end_frame'] - scene['start_frame'] != scene['duration_frames'] or scene['duration_frames'] <= 0:
                raise TimelineError('Scene duration mismatch')
            if voice is not None:
                if voice['start_sample'] < prev_voice_end or voice['duration_samples'] <= 0 or voice['end_sample'] != voice['start_sample'] + voice['duration_samples']:
                    raise TimelineError('Voice overlap or invalid voice length')
                if voice['start_sample'] < frame_to_sample(scene['start_frame'], fps, sr) or voice['end_sample'] > frame_to_sample(scene['end_frame'], fps, sr):
                    raise TimelineError('Voice not contained in visual scene')
                prev_voice_end = voice['end_sample']
            overlap = scene['transition_out']['duration_frames']
            if type(overlap) is not int or overlap < 0:
                raise TimelineError('Invalid transition frame count')
            if i + 1 < len(visual):
                if visual[i+1]['start_frame'] != scene['end_frame'] - overlap:
                    raise TimelineError('Non-contiguous scene boundary')
                if overlap > scene['tail_padding_frames'] + visual[i+1]['head_padding_frames']:
                    raise TimelineError('Transition exceeds padding')
                prior_overlap = visual[i-1]['transition_out']['duration_frames'] if i else 0
                if prior_overlap + overlap > scene['duration_frames'] - 1:
                    raise TimelineError('Adjacent transitions consume the current scene')
            elif overlap != 0:
                raise TimelineError('Last scene must not have transition out')
        total = visual[-1]['end_frame']
        if timeline['total_frames'] != total or timeline['total_samples'] != frame_to_sample(total, fps, sr) or prev_voice_end > timeline['total_samples']:
            raise TimelineError('Incorrect total duration')
    except (KeyError, TypeError, IndexError, ValueError, ZeroDivisionError) as exc:
        if isinstance(exc, TimelineError):
            raise
        raise TimelineError(f'Malformed timeline: {exc}') from exc
