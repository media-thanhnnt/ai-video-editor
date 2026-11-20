"""Phase 1.6: sample-accurate per-scene voice assembly and MP4 muxing.

All voice streams are decoded to 48kHz (or the timeline's sample rate)
16-bit stereo PCM before placement, avoiding MP3 encoder-delay concatenation.
The frame/sample timeline is authoritative; no narration is trimmed or stretched.
"""
from __future__ import annotations

import json
import shutil
import os
import subprocess
import tempfile
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

from .assets import validate_assets
from .project import Project, ValidationError, atomic_write_json, ensure_inside, read_json
from .timeline import build_timeline, validate_timeline
from .planner import validate_editing_plan, require_approved_plan
from .renderer import verify_video, RenderError, verify_render_checkpoint
from .ffmpeg_support import check_ffmpeg


class AudioError(ValidationError):
    pass


def _run(cmd: list[str], timeout: int = 1800, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError(f'Failed to run {cmd[0]}: {exc}') from exc
    if result.returncode:
        raise AudioError(f'{cmd[0]} failed (exit={result.returncode}): {result.stderr[-2400:]}')
    return result.stdout


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(_run(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(path)]))


def _write_silence(wav: wave.Wave_write, frames: int, channels: int) -> None:
    block = b'\0' * (min(frames, 32768) * channels * 2)
    while frames > 0:
        n = min(frames, 32768)
        wav.writeframesraw(block[:n * channels * 2])
        frames -= n


def _write_voice(wav: wave.Wave_write, decoded: Path, expected: int,
                 sample_rate: int, channels: int, scene_id: str) -> None:
    with wave.open(str(decoded), 'rb') as src:
        if (src.getframerate(),src.getnchannels(),src.getsampwidth()) != (sample_rate, channels, 2):
            raise AudioError(f'Scene {scene_id}: incorrect normalized PCM format')
        if src.getnframes() != expected:
            raise AudioError(f'Scene {scene_id}: decoded {src.getnframes()} samples, timeline expects {expected}. '
                             'Rebuild assets and timeline; refusing to trim/stretch the voice.')
        remain = expected
        while remain:
            n = min(remain, 32768)
            data = src.readframes(n)
            if len(data) != n * channels * 2:
                raise AudioError(f'Scene {scene_id}: truncated PCM input')
            wav.writeframesraw(data)
            remain -= n


def build_voice_mix(project: Project, timeline: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Write PCM16 WAV on the exact timeline, without mixing or clipping voice."""
    validate_timeline(timeline)
    sample_rate = int(timeline['time_base']['audio_sample_rate'])
    channels = int(project.config['output']['audio_channels'])
    if channels not in (1, 2):
        raise AudioError('Phase 1.6 supports mono or stereo output')
    total = int(timeline['total_samples'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voice-decode-',dir=destination.parent) as temp:
        tmp = Path(temp)
        staged = destination.with_name(destination.stem+'.partial.wav')
        offset = 0
        summary = []
        try:
            with wave.open(str(staged), 'wb') as output:
                output.setnchannels(channels)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                for index, voice in enumerate(timeline['voice_track']):
                    start, duration = int(voice['start_sample']), int(voice['duration_samples'])
                    if start < offset or start + duration > total:
                        raise AudioError(f'Voice {voice["scene_id"]}: overlaps or exceeds timeline')
                    _write_silence(output,start-offset,channels)
                    original = ensure_inside(project.folder,voice['source'])
                    decoded = tmp / f'{index:04d}.wav'
                    _run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
                          '-i',str(original),'-map','0:a:0','-vn',
                          '-ar',str(sample_rate),'-ac',str(channels),
                          '-c:a','pcm_s16le',str(decoded)])
                    _write_voice(output,decoded,duration,sample_rate,channels,voice['scene_id'])
                    offset = start + duration
                    summary.append({'scene_id':voice['scene_id'],'start_sample':start,
                                    'duration_samples':duration,'end_sample':offset})
                _write_silence(output,total-offset,channels)
            with wave.open(str(staged),'rb') as check:
                if check.getnframes()!=total:
                    raise AudioError('PCM output sample count differs from timeline')
            os.replace(staged,destination)
        finally:
            staged.unlink(missing_ok=True)
    return {'sample_rate':sample_rate,'channels':channels,'total_samples':total,
            'voice_segments':summary,'wav':str(destination)}


def inspect_final(path: Path, timeline: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    data = _probe(path)
    videos = [x for x in data['streams'] if x['codec_type']=='video']
    audios = [x for x in data['streams'] if x['codec_type']=='audio']
    if len(videos)!=1 or len(audios)!=1:
        raise AudioError(f'Expected exactly 1 video + 1 audio track, got {len(videos)} + {len(audios)}')
    video,audio=videos[0],audios[0]
    fps=Fraction(timeline['time_base']['fps'])
    if (video.get('codec_name'),video.get('pix_fmt')) != ('h264','yuv420p'):
        raise AudioError('Final video is not H.264/yuv420p')
    if Fraction(video['avg_frame_rate'])!=fps or (video['width'],video['height']) != (timeline['resolution']['width'],timeline['resolution']['height']):
        raise AudioError('Video dimensions or FPS do not match timeline')
    frame_count=int(video.get('nb_read_frames') or video.get('nb_frames') or 0)
    if frame_count!=int(timeline['total_frames']):
        raise AudioError(f'Final video frame count mismatch: {frame_count}/{timeline["total_frames"]}')
    if (audio.get('codec_name'),audio.get('profile')) != ('aac','LC'):
        raise AudioError('Final audio is not AAC-LC')
    if int(audio['sample_rate'])!=int(timeline['time_base']['audio_sample_rate']) or int(audio['channels'])!=int(config['output']['audio_channels']):
        raise AudioError('Audio sample rate/channels mismatch')
    if any(video.get(k)!='bt709' for k in ('color_space','color_primaries','color_transfer')):
        raise AudioError('Video BT.709 metadata missing')
    with tempfile.TemporaryDirectory(prefix='audio-qa-') as temp:
        decoded=Path(temp)/'audio.pcm'
        _run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
              '-i',str(path),'-map','0:a:0','-vn','-f','s16le','-acodec','pcm_s16le',
              '-ac',str(config['output']['audio_channels']),'-ar',str(timeline['time_base']['audio_sample_rate']),str(decoded)])
        samples=decoded.stat().st_size//(2*int(config['output']['audio_channels']))
    expected=timeline['total_samples']
    delta=samples-expected
    # AAC stores data in 1024-sample frames; account for priming/padding on decode.
    if abs(delta)>2048:
        raise AudioError(f'AAC decoded duration diverges from timeline: {delta} samples')
    return {'video_codec':'h264','video_pixel_format':'yuv420p','video_frames':frame_count,'fps':str(fps),
            'resolution':f'{video["width"]}x{video["height"]}',
            'audio_codec':'aac','audio_profile':audio['profile'],
            'sample_rate':int(audio['sample_rate']),'channels':int(audio['channels']),
            'decoded_audio_samples':samples,'expected_audio_samples':expected,
            'aac_sample_difference':delta,'color_space':video.get('color_space')}


def _srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, r = divmod(milliseconds, 3600000)
    minutes, r = divmod(r, 60000)
    secs, ms = divmod(r, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}'


def prepare_subtitles(project: Project, timeline: dict[str, Any]) -> Path | None:
    """Author SRT from storyboard cues or import an SRT; never infer speech timing."""
    board = project.storyboard
    source = board.get('subtitle_file')
    cues = board.get('subtitles', [])
    if not source and not cues:
        return None
    destination = project.folder / 'output' / 'subtitles.srt'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source:
        src = ensure_inside(project.folder, source)
        try:
            content = src.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as exc:
            raise AudioError(f'Unable to read subtitle file: {exc}') from exc
        import re
        timecode = re.compile(r'^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$')
        def read_time(raw: str) -> float:
            match = timecode.fullmatch(raw.strip())
            if match is None:
                raise AudioError(f'Invalid SRT timestamp: {raw!r}')
            hours, minutes, seconds, millis = map(int, match.groups())
            if minutes >= 60 or seconds >= 60:
                raise AudioError(f'Invalid SRT timestamp: {raw!r}')
            return hours*3600 + minutes*60 + seconds + millis/1000
        entries = re.findall(r'(?m)^\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})(?:[^\n]*)$', content)
        if not entries:
            raise AudioError('SRT file has no valid subtitle timing cues')
        last_start = -1.0
        for index, (from_text,to_text) in enumerate(entries, 1):
            start, end = read_time(from_text), read_time(to_text)
            if not 0 <= start < end <= timeline['total_duration_seconds'] + 0.001:
                raise AudioError(f'SRT cue {index} is outside video duration or has invalid range')
            if start < last_start:
                raise AudioError(f'SRT cue {index} is not ordered by start time')
            last_start = start
    else:
        lines = []
        end_of_video = timeline['total_duration_seconds']
        for index, cue in enumerate(cues, 1):
            if cue['end_seconds'] > end_of_video + 0.001:
                raise AudioError(f'Subtitle {index} extends beyond the video')
            text = cue['text'].replace('\r', '').strip()
            lines.append(f'{index}\n{_srt_timestamp(cue["start_seconds"])} --> {_srt_timestamp(cue["end_seconds"])}\n{text}\n')
        content = '\n'.join(lines)
    partial = destination.with_suffix('.partial.srt')
    partial.write_text(content, encoding='utf-8')
    os.replace(partial,destination)
    return destination


def _source_video_audio_layers(project: Project, timeline: dict[str, Any], assets: dict[str, Any],
                               sample_rate: int, channels: int, first_input: int) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Build sample-aligned source-audio layers, with equal-power-ish linear dissolve ramps.

    Source video is normalized from time zero to its scene's *visual* frame count;
    any missing tail is padded with silence. Overlap is defined by timeline
    transition frames, not scene duration sums, and is converted to exact samples.
    """
    fps = Fraction(timeline['time_base']['fps'])
    inputs: list[str] = []
    filters: list[str] = []
    details: list[dict[str, Any]] = []
    visual = timeline['video_track']
    storyboard = project.storyboard['scenes']
    for index, (scene, clip) in enumerate(zip(storyboard, visual)):
        if scene.get('type') != 'video_clip':
            continue
        setting = scene.get('source_audio', {})
        if not setting.get('enabled', True):
            continue
        file_info = assets['file_results'][scene['video']]['info']
        if not file_info['has_audio']:
            if setting.get('enabled') is True:
                raise AudioError(f'Scene {scene["scene_id"]}: explicitly requested source audio but input is silent')
            continue
        file_path = ensure_inside(project.folder, scene['video'])
        input_index = first_input + len(details)
        inputs.extend(['-i', str(file_path)])
        start_frame = int(clip['start_frame'])
        duration_frames = int(clip['duration_frames'])
        # Derive rational sample positions independently of float timing fields.
        start_sample = round(Fraction(start_frame, 1) * sample_rate / fps)
        end_sample = round(Fraction(start_frame + duration_frames, 1) * sample_rate / fps)
        duration_samples = end_sample - start_sample
        incoming = int(visual[index-1]['transition_out']['duration_frames']) if index else 0
        outgoing = int(clip['transition_out']['duration_frames'])
        fade_in_samples = min(duration_samples, round(Fraction(incoming, 1) * sample_rate / fps))
        fade_out_samples = min(duration_samples, round(Fraction(outgoing, 1) * sample_rate / fps))
        # FFmpeg afade accepts sample indices; avoid fractional-second drift at 30000/1001 FPS.
        chain = (f'[{input_index}:a:0]aresample={sample_rate},'
                 f'aformat=sample_fmts=fltp:channel_layouts={"stereo" if channels == 2 else "mono"},'
                 f'atrim=start=0,asetpts=PTS-STARTPTS,apad,atrim=end_sample={duration_samples},'
                 f'asetpts=N/SR/TB')
        if fade_in_samples:
            chain += f',afade=t=in:ss=0:ns={fade_in_samples}'
        if fade_out_samples:
            chain += f',afade=t=out:ss={duration_samples-fade_out_samples}:ns={fade_out_samples}'
        chain += f',volume={float(setting.get("volume", 1.0))}'
        # 'S' means exact samples, not milliseconds (FFmpeg adelay).
        chain += f',adelay={start_sample}S:all=1[srcaudio{len(details)}]'
        filters.append(chain)
        details.append({'scene_id':scene['scene_id'], 'source':scene['video'],
                        'start_frame':start_frame, 'start_sample':start_sample,
                        'duration_samples':duration_samples, 'fade_in_samples':fade_in_samples,
                        'fade_out_samples':fade_out_samples, 'volume':float(setting.get('volume',1))})
    return inputs, filters, details


def mux_project_audio(project: Project) -> dict[str, Any]:
    """Use Phase 1.5 video cache and Phase 1.3 timeline to build final.mp4."""
    check_ffmpeg(project)
    plan = validate_editing_plan(project, read_json(project.folder/'editing_plan.json'))
    require_approved_plan(project,plan)
    timeline = read_json(project.folder/'timeline.json')
    validate_timeline(timeline)
    assets=validate_assets(project,write_report=True)
    if assets['status']!='PASS':
        raise AudioError('Assets invalid. Check reports/assets_report.json')
    current=build_timeline(project,asset_report=assets,editing_plan=plan,write_output=False)
    for key in ('time_base','resolution','video_track','voice_track','total_frames','total_samples','planning_cache_key','input_hashes'):
        if current[key]!=timeline.get(key):
            raise AudioError(f'Stale timeline ({key}); run ai-video-editor timeline PROJECT --use-plan')
    video=project.folder/'output'/'video_only.mp4'
    if not video.is_file():
        raise AudioError('Video missing: run ai-video-editor render PROJECT first (Phase 1.5)')
    verify_render_checkpoint(project,plan,timeline,video)
    resolution=timeline['resolution']
    try:
        video_qa=verify_video(video,int(timeline['total_frames']),int(resolution['width']),
                              int(resolution['height']),Fraction(timeline['time_base']['fps']),final=True)
    except RenderError as exc:
        raise AudioError(f'Video-only input validation failed: {exc}') from exc
    output=project.folder/'output'/'final.mp4'
    pcm=project.folder/'work'/'audio'/'voice_mix.wav'
    pcm.parent.mkdir(parents=True,exist_ok=True)
    mix=build_voice_mix(project,timeline,pcm)
    staged=output.with_name('final.partial.mp4')
    try:
        subtitle_path = prepare_subtitles(project, timeline)
        tracks = project.storyboard.get('audio_tracks', [])
        inputs = ['-i',str(video), '-i',str(pcm)]
        for track in tracks:
            inputs.extend(['-i',str(ensure_inside(project.folder, track['file']))])
        source_inputs, source_filters, source_details = _source_video_audio_layers(
            project, timeline, assets, mix['sample_rate'], mix['channels'], 2 + len(tracks))
        inputs.extend(source_inputs)
        mix_filter = []
        for i, track in enumerate(tracks):
            delay = round(track['start_seconds'] * mix['sample_rate'])
            volume = track['volume']
            mix_filter.append(f'[{i+2}:a:0]aresample={mix["sample_rate"]},'
                              f'aformat=channel_layouts={"stereo" if mix["channels"] == 2 else "mono"},'
                              f'volume={volume},adelay={delay}S:all=1[layer{i}]')
        mix_filter.extend(source_filters)
        if tracks or source_details:
            layers = ''.join(f'[layer{i}]' for i in range(len(tracks)))
            layers += ''.join(f'[srcaudio{i}]' for i in range(len(source_details)))
            count = 1 + len(tracks) + len(source_details)
            peak_db = float(project.config['audio']['true_peak_max_dbtp'])
            limit = min(1.0, max(0.0625, 10 ** (peak_db / 20)))
            mix_filter.append(f'[1:a:0]{layers}amix=inputs={count}:duration=first:normalize=0,'
                              f'atrim=end_sample={timeline["total_samples"]},asetpts=N/SR/TB,'
                              f'alimiter=limit={limit:.8f}:level=0:latency=1[aout]')
        audio_map = ['-map','[aout]'] if mix_filter else ['-map','1:a:0']
        cmd = ['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y'] + inputs
        if mix_filter:
            cmd += ['-filter_complex',';'.join(mix_filter)]
        cmd += ['-map','0:v:0'] + audio_map + ['-c:v','copy']
        cmd += ['-c:a','aac','-profile:a','aac_low','-b:a','192k',
                '-ar',str(mix['sample_rate']),'-ac',str(mix['channels']),
                '-movflags','+faststart',str(staged)]
        _run(cmd,timeout=3600,cwd=project.folder)
        inspection=inspect_final(staged,timeline,project.config)
        os.replace(staged,output)
    finally:
        staged.unlink(missing_ok=True)
    report={'phase':'1.10.3','status':'PASS','project_id':project.project_id,
            'output':str(output),'video_source':str(video),'pcm_source':str(pcm),
            'video':video_qa,'audio':mix,'final_qa':inspection,
            'audio_scope':'per_scene_voice_plus_tracks_plus_source_video_audio',
            'source_video_audio':source_details,
            'additional_audio_tracks':len(project.storyboard.get('audio_tracks',[])),
            'subtitles':str(subtitle_path) if subtitle_path else None,
            'warnings':timeline.get('warnings',[])}
    atomic_write_json(project.folder/'reports'/'audio_report.json',report)
    return report
