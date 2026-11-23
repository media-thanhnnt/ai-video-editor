"""Phase 1.5: deterministic, video-only scene renderer and transition assembler.

Motion is measured in output pixel coordinates, evaluated independently at each
frame and resampled with OpenCV Lanczos4 (accepted Phase 0.2 method).
Audio deliberately belongs to Phase 1.6.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import math
import os
import subprocess
import time
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageFilter

from .assets import validate_assets
from .planner import validate_editing_plan, require_approved_plan
from .project import (Project, ValidationError, atomic_write_json, ensure_inside, read_json,
                      DEFAULT_ZOOM, QUALITY_CRF)
from .timeline import build_timeline, validate_timeline
from .ffmpeg_support import check_ffmpeg, complex_script_args

ENGINE_VERSION = '1.5.0'  # preserve legacy identity for non-image media
IMAGE_MOTION_CACHE_VERSION = 'image-motion-1.10.3'  # changed pan algorithm; invalidate old static image caches
CHECKPOINT_VERSION = '1.7.0'

class RenderError(ValidationError):
    pass


def _exec(cmd: list[str], *, timeout: int = 1200, cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f'Could not run {cmd[0]}: {exc}') from exc
    if proc.returncode:
        raise RenderError(f'{cmd[0]} exited {proc.returncode}: {proc.stderr[-1800:]}')
    return proc.stdout


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(_exec(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def verify_video(path: Path, frames: int, width: int, height: int, fps: Fraction,
                 *, final: bool = False) -> dict[str, Any]:
    data = _probe(path)
    video = [s for s in data['streams'] if s['codec_type'] == 'video']
    audio = [s for s in data['streams'] if s['codec_type'] == 'audio']
    if len(video) != 1 or audio:
        raise RenderError(f'Expected video-only file, got video={len(video)}, audio={len(audio)}')
    s = video[0]
    count = int(s.get('nb_read_frames') or s.get('nb_frames') or 0)
    actual_fps = Fraction(s['avg_frame_rate'])
    if count != frames or (s['width'], s['height']) != (width,height) or actual_fps != fps:
        raise RenderError(f'Video mismatch in {path.name}: frames={count}/{frames}, dimensions={s["width"]}x{s["height"]}, fps={actual_fps}/{fps}')
    if final and (s['codec_name'] != 'h264' or s['pix_fmt'] != 'yuv420p'):
        raise RenderError('Final output must be H.264 yuv420p')
    if final and any(s.get(k) != 'bt709' for k in ('color_space','color_transfer','color_primaries')):
        raise RenderError('Final output must signal BT.709: ' + str({k:s.get(k) for k in ('color_space','color_transfer','color_primaries')}))
    return {'frames':count, 'codec':s['codec_name'], 'pix_fmt':s['pix_fmt'], 'fps':str(actual_fps),
            'width':width,'height':height, 'color_space':s.get('color_space')}


AUTO_FIT_TOLERANCE = 0.03  # aspect mismatch (relative) that cover may crop without losing content


def _resolve_fit(mode: str, image_width: int, image_height: int, width: int, height: int) -> str:
    """'auto' = cover when the aspect ratio (nearly) matches, otherwise blur_fill.

    Example: a 1536x1024 (3:2) image in a 16:9 frame would lose 15.6% of its height
    with cover; auto keeps the whole picture over a blurred background instead.
    """
    if mode != 'auto':
        return mode
    mismatch = abs((image_width / image_height) / (width / height) - 1)
    return 'cover' if mismatch <= AUTO_FIT_TOLERANCE else 'blur_fill'


def _prepare_source(path: Path, width: int, height: int, factor: int, mode: str) -> np.ndarray:
    tw, th = width * factor, height * factor
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGB')
    mode = _resolve_fit(mode, image.width, image.height, width, height)
    if mode == 'cover':
        canvas = ImageOps.fit(image, (tw,th), method=Image.Resampling.LANCZOS)
    elif mode == 'contain':
        canvas = Image.new('RGB',(tw,th),(0,0,0))
        fit = ImageOps.contain(image,(tw,th),method=Image.Resampling.LANCZOS)
        canvas.paste(fit,((tw-fit.width)//2,(th-fit.height)//2))
    elif mode == 'blur_fill':
        bg = ImageOps.fit(image,(tw,th),method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=max(5, factor*12)))
        fit = ImageOps.contain(image,(tw,th),method=Image.Resampling.LANCZOS)
        bg.paste(fit,((tw-fit.width)//2,(th-fit.height)//2))
        canvas=bg
    else:
        raise RenderError(f'Unknown fit mode: {mode}')
    return np.asarray(canvas, dtype=np.uint8)


def _load_overlay(path: Path, width: int, height: int) -> np.ndarray:
    """RGBA overlay → premultiplied float layer (H, W, 4): rgb*alpha and 1-alpha.

    The overlay is stretched to the output size; create it at the output resolution
    (overlay-generate does) to avoid any resampling.
    """
    with Image.open(path) as opened:
        layer = opened.convert('RGBA')
    if layer.size != (width, height):
        layer = layer.resize((width, height), Image.Resampling.LANCZOS)
    rgba = np.asarray(layer, dtype=np.float32) / 255.0
    alpha = rgba[..., 3:4]
    return np.concatenate([rgba[..., :3] * alpha * 255.0, 1.0 - alpha], axis=2)


def _composite(pixels: np.ndarray, overlay: np.ndarray) -> np.ndarray:
    blended = pixels.astype(np.float32) * overlay[..., 3:4] + overlay[..., :3]
    return np.clip(blended + 0.5, 0, 255).astype(np.uint8)


def _smoothstep(t: float) -> float:
    return t*t*(3-2*t)


def _crf(cfg: dict[str, Any]) -> int:
    """config.output.quality → x264 CRF (high = 18, the historical fixed value)."""
    return QUALITY_CRF[cfg['output'].get('quality', 'high')]


def _motion_settings(cfg: dict[str, Any]) -> tuple[dict[str, float], str]:
    motion = cfg.get('motion', {})
    zoom = {k: float(v) for k, v in motion.get('zoom_factor', DEFAULT_ZOOM).items()}
    return zoom, motion.get('easing', 'ease_in_out')


def _motion_key(cfg: dict[str, Any], intensity: str | None = None) -> dict[str, Any]:
    """Extra cache-key material, empty for default settings so existing caches stay valid."""
    zoom, easing = _motion_settings(cfg)
    if intensity is not None:
        changed = zoom[intensity] != DEFAULT_ZOOM[intensity]
        return {'zoom': zoom[intensity], 'easing': easing} if changed or easing != 'ease_in_out' else {}
    if zoom != DEFAULT_ZOOM or easing != 'ease_in_out':
        return {'zoom_factor': zoom, 'easing': easing}
    return {}


def motion_frame(source: np.ndarray, kind: str, intensity: str, frame: int,
                 total: int, width: int, height: int, *,
                 zoom_factors: dict[str, float] | None = None,
                 easing: str = 'ease_in_out') -> np.ndarray:
    """Inverse affine subpixel sample from fixed high-res source; no cumulative motion."""
    if kind not in ('static', 'zoom_in','zoom_out','pan_left','pan_right'):
        raise RenderError(f'Unsupported motion: {kind}')
    if intensity not in ('subtle','medium','strong') or total <= 0 or not 0 <= frame < total:
        raise RenderError('Invalid intensity or frame index')
    ratio = (zoom_factors or DEFAULT_ZOOM)[intensity]
    progress = frame/(total-1) if total>1 else 0.0
    u = progress if easing == 'linear' else _smoothstep(progress)
    if kind == 'static':
        zoom=1.0
    elif kind == 'zoom_in':
        zoom=1+(ratio-1)*u
    elif kind == 'zoom_out':
        zoom=ratio-(ratio-1)*u
    else:
        zoom=ratio
    sh,sw=source.shape[:2]
    scale_x=sw/width/zoom
    scale_y=sh/height/zoom
    spare=(sw-width*scale_x)/2
    center_x=sw/2
    if kind=='pan_left':
        center_x += spare*(1-2*u)
    elif kind=='pan_right':
        center_x += spare*(-1+2*u)
    c=center_x-scale_x*width/2+(scale_x-1)/2
    f=sh/2-scale_y*height/2+(scale_y-1)/2
    affine=np.array([[scale_x,0.,c],[0.,scale_y,f]], dtype=np.float64)
    return cv2.warpAffine(source,affine,(width,height),
                         flags=cv2.INTER_LANCZOS4|cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_REPLICATE)


def _encode_scene(source: np.ndarray, destination: Path, kind: str, intensity: str,
                  frames: int, width: int, height: int, fps: Fraction, *,
                  zoom_factors: dict[str, float] | None = None, easing: str = 'ease_in_out',
                  overlay: np.ndarray | None = None) -> None:
    destination.parent.mkdir(parents=True,exist_ok=True)
    cmd=['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y','-f','rawvideo',
         '-pixel_format','rgb24','-video_size',f'{width}x{height}', '-framerate',
         f'{fps.numerator}/{fps.denominator}', '-i','pipe:0','-an',
         '-c:v','ffv1','-level','3','-pix_fmt','bgr0',str(destination)]
    try:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    except OSError as exc:
        raise RenderError(f'FFmpeg unavailable: {exc}') from exc
    try:
        assert proc.stdin is not None
        for i in range(frames):
            pixels = motion_frame(source,kind,intensity,i,frames,width,height,
                                  zoom_factors=zoom_factors,easing=easing)
            if overlay is not None:
                # Text/graphics layer is composited AFTER motion: it never zooms or pans.
                pixels = _composite(pixels, overlay)
            proc.stdin.write(pixels.tobytes())
        proc.stdin.close()
        assert proc.stderr is not None
        stderr=proc.stderr.read().decode('utf-8','replace')
        rc=proc.wait()
        if rc:
            raise RenderError(f'Scene encode failed: {stderr[-1800:]}')
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc.stderr is not None:
            proc.stderr.close()


def _encode_sequence_scene(project: Project, scene: dict[str, Any], destination: Path,
                           frames: int, width: int, height: int, fps: Fraction, fit: str) -> None:
    """Encode contiguous still frames exactly once, without camera motion or interpolation."""
    from .assets import sequence_frame_paths
    from .sequence import fit_filter
    paths = sequence_frame_paths(scene)
    if len(paths) != frames:
        raise RenderError('Sequence count differs from timeline')
    for rel in paths:
        if not ensure_inside(project.folder, rel).is_file():
            raise RenderError(f'Missing sequence frame: {rel}')
    seq = scene['sequence']
    source_dir = ensure_inside(project.folder, seq['frames_dir'])
    input_pattern = str(source_dir / seq['frame_pattern'])
    vf = fit_filter(width, height, fit) + ',format=bgr0'
    _exec(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
           '-framerate',f'{fps.numerator}/{fps.denominator}', '-start_number','1',
           '-i',input_pattern,'-frames:v',str(frames),'-vf',vf,
           '-an','-c:v','ffv1','-level','3','-pix_fmt','bgr0',str(destination)],timeout=3600)


def _encode_video_scene(project: Project, scene: dict[str, Any], destination: Path,
                        frames: int, width: int, height: int, fps: Fraction, fit: str) -> None:
    """Decode an existing video (discard original audio), normalize frame rate and frame count."""
    from .sequence import fit_filter
    source = ensure_inside(project.folder, scene['video'])
    vf = f'fps={fps.numerator}/{fps.denominator},' + fit_filter(width, height, fit) + ',tpad=stop_mode=clone:stop=-1,format=bgr0'
    _exec(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
           '-i',str(source),'-an','-vf',vf,'-frames:v',str(frames),
           '-c:v','ffv1','-level','3','-pix_fmt','bgr0',str(destination)],timeout=3600)


def _ffv1_trim(src: Path, out: Path, start: int, end: int, fps: Fraction) -> None:
    if end <= start:
        return
    _exec(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y','-i',str(src),
           '-vf',f'trim=start_frame={start}:end_frame={end},setpts=N/({fps.numerator}/{fps.denominator}*TB)',
           '-an','-c:v','ffv1','-level','3','-pix_fmt','bgr0',str(out)])


def _transition(left: Path, right: Path, out: Path, left_frames: int,
                overlap: int, style: str, fps: Fraction) -> None:
    # Each input is trimmed to EXACTLY 'overlap' frames; xfade output is same length.
    # xfade transition 'fade' implements crossfade. FFmpeg 'dissolve' uses stochastic blend.
    transition_name = 'fade' if style=='crossfade' else 'dissolve'
    g=(f'[0:v]trim=start_frame={left_frames-overlap}:end_frame={left_frames},setpts=PTS-STARTPTS,fps={fps.numerator}/{fps.denominator},settb=AVTB,format=yuv444p[a];'
       f'[1:v]trim=start_frame=0:end_frame={overlap},setpts=PTS-STARTPTS,fps={fps.numerator}/{fps.denominator},settb=AVTB,format=yuv444p[b];'
       f'[a][b]xfade=transition={transition_name}:duration={float(Fraction(overlap,1)/fps):.9f}:offset=0,'
       f'fps={fps.numerator}/{fps.denominator},trim=end_frame={overlap},setpts=N/({fps.numerator}/{fps.denominator}*TB)[v]')
    _exec(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
           '-i',str(left),'-i',str(right),'-filter_complex',g,'-map','[v]',
           '-an','-c:v','ffv1','-level','3','-pix_fmt','bgr0',str(out)])


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()




def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _render_lock(work: Path):
    """Refuse simultaneous renders, and recover stale locks from dead local PIDs.

    Lock is best-effort on Linux/Windows. Never delete a lock owned by a
    running process; cross-host shared folders require an external coordinator.
    """
    lock = work / 'render.lock'
    def alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == 'nt':
            # Windows os.kill(pid, 0) is NOT a safe existence probe: for a
            # console process it can issue CTRL_C_EVENT and interrupt our own
            # unittest/render process (STATUS_CONTROL_C_EXIT 0xC000013A).
            # Query the process handle instead; never send a signal.
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            wait = kernel32.WaitForSingleObject
            wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            wait.restype = wintypes.DWORD
            close = kernel32.CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            close.restype = wintypes.BOOL

            SYNCHRONIZE = 0x00100000
            WAIT_OBJECT_0 = 0x00000000
            WAIT_TIMEOUT = 0x00000102
            ERROR_INVALID_PARAMETER = 87
            ERROR_ACCESS_DENIED = 5
            handle = open_process(SYNCHRONIZE, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == ERROR_INVALID_PARAMETER:
                    return False  # PID does not exist
                # Permission-denied or unknown error: treat as alive to avoid
                # accidentally stealing a lock owned by another process.
                return True
            try:
                status = wait(handle, 0)
                if status == WAIT_OBJECT_0:
                    return False  # Existing process has terminated
                return True  # WAIT_TIMEOUT means active; unknown = safe lock
            finally:
                close(handle)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                detail = json.loads(lock.read_text(encoding='utf-8'))
                pid = int(detail['pid'])
                host = detail.get('host')
                # Only assess stale PIDs on the same machine.
                import socket
                if host != socket.gethostname() or alive(pid):
                    raise RenderError(f'Another render may be running (lock: {lock}).')
                lock.unlink()
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise RenderError(f'Render lock exists and cannot be recovered safely: {lock}; {exc}') from exc
    else:
        raise RenderError(f'Could not acquire render lock: {lock}')
    try:
        import socket
        os.write(fd, json.dumps({'pid': os.getpid(), 'host': socket.gethostname()}).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _checkpoint_path(project: Project, path: Path) -> str:
    """Keep checkpoint paths portable when moving a whole project directory."""
    return path.resolve().relative_to(project.folder.resolve()).as_posix()


def _checkpoint_matches_output(project: Project, stored: object, expected: Path) -> bool:
    """Accept portable paths or legacy absolute paths for the same output slot.

    Legacy absolute path can differ after relocation; identity + file SHA-256
    remain mandatory, so a stale or altered artifact cannot be accepted.
    """
    if not isinstance(stored, str):
        return False
    expected_relative = _checkpoint_path(project, expected)
    if stored == expected_relative or stored == str(expected.resolve()):
        return True
    from pathlib import PureWindowsPath
    old = PureWindowsPath(stored) if (len(stored) > 1 and stored[1] == ':') else Path(stored)
    return old.is_absolute() and old.name == expected.name and old.parent.name == expected.parent.name


def verify_render_checkpoint(project: Project, plan: dict[str, Any],
                             timeline: dict[str, Any], video: Path) -> dict[str, Any]:
    """Reject stale, incomplete or externally modified video-only output."""
    path = project.folder / 'reports' / 'render_checkpoint.json'
    if not path.is_file():
        raise RenderError('Render checkpoint missing; run render before audio/qa')
    state = read_json(path)
    if state.get('status') != 'PASS':
        raise RenderError('Render checkpoint is not PASS; rerun render')
    factor = state.get('factor')
    if type(factor) is not int or factor not in (1,2,3,4):
        raise RenderError('Render checkpoint has no valid supersample factor; rerun render')
    identity = compute_render_identity(project,plan,timeline,factor)
    if state.get('render_key') != identity:
        raise RenderError('Stale render checkpoint identity; rerun render')
    saved = state.get('output', {})
    if not _checkpoint_matches_output(project, saved.get('file'), video) or not saved.get('sha256'):
        raise RenderError('Render checkpoint output does not match video-only MP4')
    if not video.is_file() or _sha256_file(video) != saved['sha256']:
        raise RenderError('Video-only MP4 differs from render checkpoint SHA-256')
    return {'render_key':identity, 'sha256':saved['sha256'], 'factor':factor}


def _use_cached_video(path: Path, frames: int, width: int, height: int, fps: Fraction,
                      *, final: bool = False, expected_hash: str | None = None) -> bool:
    if not path.is_file():
        return False
    try:
        verify_video(path, frames, width, height, fps, final=final)
        if expected_hash is not None and _sha256_file(path) != expected_hash:
            return False
        return True
    except (RenderError, ValueError, OSError, KeyError, json.JSONDecodeError):
        return False


def _checkpoint_write(path: Path, state: dict[str, Any], step: str) -> None:
    state['last_step'] = step
    state['updated_utc'] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(path, state)

def render_project(project: Project, *, output_file: Path | None = None,
                   force: bool = False, supersample_factor: int | None = None) -> dict[str, Any]:
    check_ffmpeg(project)
    plan = validate_editing_plan(project,read_json(project.folder/'editing_plan.json'))
    require_approved_plan(project,plan)
    timeline = read_json(project.folder/'timeline.json')
    validate_timeline(timeline)
    report=validate_assets(project,write_report=True)
    if report['status']!='PASS':
        raise RenderError('Invalid assets; inspect assets_report.json')
    # Reject stale timeline; do not quietly render with obsolete voice/transition timing.
    fresh=build_timeline(project,asset_report=report,editing_plan=plan,write_output=False)
    comparable=('time_base','resolution','video_track','voice_track','total_frames','total_samples','planning_cache_key','input_hashes')
    for key in comparable:
        if fresh[key]!=timeline.get(key):
            raise RenderError(f'Stale timeline ({key}); run ai-video-editor timeline PROJECT --use-plan')
    cfg=project.config
    w,h=int(cfg['output']['width']),int(cfg['output']['height'])
    factor=supersample_factor or int(cfg['motion']['supersample_factor'])
    if factor not in (1,2,3,4):
        raise RenderError('supersample factor must be 1,2,3 or 4')
    if w*h*factor*factor>140_000_000:
        raise RenderError('Supersampled source would be too large; lower factor or resolution')
    fps=Fraction(timeline['time_base']['fps'])
    tracks=timeline['video_track']
    result_file=(Path(output_file) if output_file is not None else project.folder/'output'/'video_only.mp4').resolve()
    if not result_file.is_relative_to(project.folder.resolve()):
        raise RenderError('Output file must be inside project folder')
    result_file.parent.mkdir(parents=True,exist_ok=True)
    work=project.folder/'work'
    scenes_dir=work/'scenes'; seg_dir=work/'segments'
    scenes_dir.mkdir(parents=True,exist_ok=True);seg_dir.mkdir(parents=True,exist_ok=True)
    # config.retry: re-run only the encoding stage (validation above is deterministic and is
    # never retried). Completed scene clips and segments are reused, so a retry is cheap.
    attempts = int(cfg['retry'].get('render_max_attempts', 1))
    backoff = [float(v) for v in cfg['retry'].get('backoff_seconds', [])]
    for attempt in range(1, attempts + 1):
        try:
            with _render_lock(work):
                return _render_project_locked(project, plan, timeline, report, cfg, w, h, factor, fps,
                                              tracks, result_file, scenes_dir, seg_dir, force)
        except RenderError as exc:
            if attempt >= attempts:
                raise
            wait = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0.0
            print(f'  render attempt {attempt}/{attempts} failed: {str(exc)[:300]}; '
                  f'retrying in {wait:g}s', flush=True)
            time.sleep(wait)
            force = False  # keep everything that was already verified
    raise RenderError('Render failed')  # unreachable; keeps type checkers satisfied


def compute_render_identity(project: Project, plan: dict[str, Any],
                            timeline: dict[str, Any], factor: int) -> str:
    """Identity for video pixels, duration, frame transitions and optional burned subtitles."""
    cfg = project.config
    w, h = int(cfg['output']['width']), int(cfg['output']['height'])
    fps = Fraction(timeline['time_base']['fps'])
    from .assets import sequence_frame_paths
    visuals: set[str] = set()
    for scene in project.storyboard['scenes']:
        kind = scene.get('type','image')
        if kind == 'image':
            visuals.add(scene['image'])
            if scene.get('overlay'):
                visuals.add(scene['overlay'])
        elif kind == 'video_clip':
            visuals.add(scene['video'])
        else:
            visuals.update(sequence_frame_paths(scene))
    board = project.storyboard
    burn = cfg['subtitles']['mode'] in ('burn_in','both') and (board.get('subtitles') or board.get('subtitle_file'))
    burned = None
    if burn:
        from .assets import compute_input_hashes
        font_file = cfg['subtitles'].get('font_file')
        burned = {'cues':board.get('subtitles'), 'srt_hash':(
            compute_input_hashes({'srt':ensure_inside(project.folder,board['subtitle_file'])})['srt']
            if board.get('subtitle_file') else None),
            'font_hash':(compute_input_hashes({'font':ensure_inside(project.folder,font_file)})['font']
                         if font_file and ensure_inside(project.folder,font_file).is_file() else None),
            'subtitle_config':cfg['subtitles'],
            # 2.5.1.7: MarginV from safe_area_bottom_percent + wrapped cues change burned pixels.
            'style_version':'2.5.1.7'}
    # Notably absent: input audio hashes, voice content, audio tracks, sidecar-only subtitles.
    # Settings added in 2.5.1.7 join the identity only when they differ from the
    # historical behaviour, so an unchanged project keeps its render checkpoint.
    extra = {}
    if _motion_key(cfg):
        extra['motion_settings'] = _motion_key(cfg)
    if _crf(cfg) != QUALITY_CRF['high']:
        extra['crf'] = _crf(cfg)
    return _digest({
        **extra,
        'checkpoint_version': CHECKPOINT_VERSION, 'assembly_version':'1.10.2',
        'image_motion_version':(IMAGE_MOTION_CACHE_VERSION if any(
            scene.get('type','image') == 'image' for scene in project.storyboard['scenes']) else None),
        'engine_version': ENGINE_VERSION, 'plan': plan['scenes'],
        'video_track':timeline['video_track'], 'intro':timeline['intro'],
        'outro':timeline['outro'], 'total_frames':timeline['total_frames'],
        'time_base_fps':str(fps), 'visual_hashes':{key: timeline['input_hashes'][key]
                                                 for key in sorted(visuals)},
        'burned_subtitles':burned,
        'factor':factor, 'fit':cfg['image']['fit_mode'],
        'encoder_preset':cfg['output']['encoder_preset'], 'resolution':[w,h],
    })

def _render_project_locked(project: Project, plan: dict[str, Any], timeline: dict[str, Any],
                           report: dict[str, Any], cfg: dict[str, Any], w: int, h: int,
                           factor: int, fps: Fraction, tracks: list[dict[str, Any]],
                           result_file: Path, scenes_dir: Path, seg_dir: Path,
                           force: bool) -> dict[str, Any]:
    # The complete render identity includes timing, image inputs, all transforms,
    # final encoding settings, and the current engine/checkpoint implementation.
    identity = compute_render_identity(project, plan, timeline, factor)
    checkpoint_dir = project.folder/'work'/'checkpoints'/identity
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = project.folder/'reports'/'render_checkpoint.json'
    previous = None
    if checkpoint_file.exists():
        try:
            previous = read_json(checkpoint_file)
        except (ValidationError, OSError, ValueError, json.JSONDecodeError):
            pass
    state = {
        'phase':'1.7', 'project_id': project.project_id, 'render_key': identity,
        'status':'IN_PROGRESS', 'factor':factor, 'scenes':{}, 'segments':{}, 'output':{},
        'checkpoint_dir': _checkpoint_path(project, checkpoint_dir),
    }
    _checkpoint_write(checkpoint_file, state, 'started')
    clips=[]; reuse=0
    try:
        for i, track in enumerate(tracks):
            scene=project.storyboard['scenes'][i]
            kind=plan['scenes'][i]['motion']; intensity=plan['scenes'][i]['motion_intensity']
            is_sequence = scene.get('type') == 'image_sequence'
            is_video = scene.get('type') == 'video_clip'
            if is_sequence:
                from .assets import sequence_frame_paths
                frame_paths = sequence_frame_paths(scene)
                source_identity = [report['input_hashes'][relative] for relative in frame_paths]
                key = _digest({'engine':'sequence-v1.9', 'hashes':source_identity,
                               'sequence':scene['sequence'], 'frames':track['duration_frames'],
                               'resolution':[w,h], 'fps':str(fps), 'fit':cfg['image']['fit_mode']})
            elif is_video:
                key = _digest({'engine':'video-v1.10','hash':report['input_hashes'][scene['video']],
                               'frames':track['duration_frames'],'resolution':[w,h],
                               'fps':str(fps),'fit':cfg['image']['fit_mode']})
            else:
                path=ensure_inside(project.folder, scene['image'])
                material={'engine':IMAGE_MOTION_CACHE_VERSION,'image_hash':report['input_hashes'][scene['image']],
                          'motion':kind,'intensity':intensity,'frames':track['duration_frames'],
                          'resolution':[w,h],'fps':str(fps),'factor':factor,'fit':cfg['image']['fit_mode']}
                # New fields only when used, so caches of unchanged projects stay valid.
                material.update(_motion_key(cfg, intensity))
                if scene.get('overlay'):
                    material['overlay_hash']=report['input_hashes'][scene['overlay']]
                key=_digest(material)
            clip=scenes_dir/f'{scene["scene_id"]}_{key[:16]}.mkv'
            if not force and _use_cached_video(clip, track['duration_frames'], w, h, fps):
                reuse+=1
                print(f'  cached scene {i+1}/{len(tracks)}',flush=True)
            else:
                print(f'  rendering scene {i+1}/{len(tracks)} ({kind}, {track["duration_frames"]} frames)',flush=True)
                part=clip.with_name(clip.stem+'.partial.mkv')
                try:
                    if is_sequence:
                        _encode_sequence_scene(project,scene,part,track['duration_frames'],w,h,fps,cfg['image']['fit_mode'])
                    elif is_video:
                        _encode_video_scene(project,scene,part,track['duration_frames'],w,h,fps,cfg['image']['fit_mode'])
                    else:
                        img=_prepare_source(path,w,h,factor,cfg['image']['fit_mode'])
                        zoom,easing=_motion_settings(cfg)
                        layer=(_load_overlay(ensure_inside(project.folder,scene['overlay']),w,h)
                               if scene.get('overlay') else None)
                        _encode_scene(img,part,kind,intensity,track['duration_frames'],w,h,fps,
                                      zoom_factors=zoom,easing=easing,overlay=layer)
                    verify_video(part,track['duration_frames'],w,h,fps)
                    os.replace(part,clip)
                finally:
                    part.unlink(missing_ok=True)
            clips.append(clip)
            state['scenes'][scene['scene_id']]={'key':key,'file':_checkpoint_path(project, clip),'frames':track['duration_frames'],'status':'PASS'}
            _checkpoint_write(checkpoint_file,state,f'scene_{scene["scene_id"]}')

        # Keep each independently validated body/transition permanently for recovery.
        # A different render identity uses a separate directory.
        segments=[]; reused_segments=0
        for i, track in enumerate(tracks):
            prior=tracks[i-1]['transition_out']['duration_frames'] if i else 0
            nxt=track['transition_out']['duration_frames']
            end=track['duration_frames']-nxt
            todo=[]
            if end>prior:
                todo.append(('body',end-prior,lambda output, i=i, prior=prior, end=end:
                             _ffv1_trim(clips[i],output,prior,end,fps)))
            if nxt:
                todo.append(('transition',nxt,lambda output, i=i, track=track, nxt=nxt:
                             _transition(clips[i],clips[i+1],output,track['duration_frames'],nxt,
                                         track['transition_out']['type'],fps)))
            for suffix, expected, produce in todo:
                name=f'{i:03d}_{suffix}'
                path=checkpoint_dir/f'{name}.mkv'
                # Verify existing segment metadata plus full file hash from manifest.
                old=(previous or {}).get('segments',{}).get(name,{}) if (previous or {}).get('render_key') == identity else {}
                expected_hash=old.get('sha256') if old else None
                # For checkpoints from an interrupted run, metadata is authoritative.
                can_reuse=(not force and bool(expected_hash) and
                           _use_cached_video(path,expected,w,h,fps,expected_hash=expected_hash))
                if can_reuse:
                    reused_segments+=1
                    print(f'  cached segment {name}',flush=True)
                else:
                    part=path.with_name(path.stem+'.partial.mkv')
                    try:
                        produce(part)
                        verify_video(part,expected,w,h,fps)
                        os.replace(part,path)
                    finally:
                        part.unlink(missing_ok=True)
                segments.append(path)
                state['segments'][name]={'file':_checkpoint_path(project, path),'frames':expected,
                                         'sha256':_sha256_file(path),'status':'PASS'}
                _checkpoint_write(checkpoint_file,state,f'segment_{name}')
        if not segments:
            raise RenderError('No video segments generated')

        old_output=(previous or {}).get('output',{}) if (previous or {}).get('render_key') == identity else {}
        reused_output=(not force and bool(old_output.get('sha256')) and
                       _checkpoint_matches_output(project,old_output.get('file'),result_file) and
                       _use_cached_video(result_file,timeline['total_frames'],w,h,fps,
                                         final=True,expected_hash=old_output['sha256']))
        if reused_output:
            video_info=verify_video(result_file,timeline['total_frames'],w,h,fps,final=True)
            print('  cached final video',flush=True)
        else:
            staged=result_file.with_name(result_file.stem+'.partial.mp4')
            intro=timeline['intro']['duration_frames']; outro=timeline['outro']['duration_frames']
            vf=[]
            if intro:
                vf.append(f'fade=t=in:s=0:n={intro}')
            if outro:
                vf.append(f'fade=t=out:s={timeline["total_frames"]-outro}:n={outro}')
            # Subtitle burn-in is part of the only H.264 encode (audio mux copies video).
            from .audio import prepare_subtitles
            subtitle_path = prepare_subtitles(project, timeline)
            if subtitle_path is not None and cfg['subtitles']['mode'] in ('burn_in','both'):
                subtitle_filter = 'subtitles=filename=output/subtitles.srt'
                # libass renders SRT on a 288-line script canvas; MarginV is in those units.
                safe = float(cfg['subtitles'].get('safe_area_bottom_percent', 8))
                styles = [f'MarginV={round(288 * safe / 100)}']
                font_path = cfg['subtitles'].get('font_file')
                if font_path and ensure_inside(project.folder,font_path).is_file():
                    from PIL import ImageFont
                    font = ensure_inside(project.folder,font_path)
                    try:
                        family = ImageFont.truetype(str(font), 14).getname()[0]
                    except (OSError, ValueError) as exc:
                        raise RenderError(f'Cannot read subtitle font: {exc}') from exc
                    if any(ch in family for ch in "':,;\\"):
                        raise RenderError('Subtitle font family has invalid filter syntax characters')
                    font_dir = font.parent.relative_to(project.folder.resolve()).as_posix()
                    if any(ch in font_dir for ch in "\\':,;[]"):  # safe libavfilter syntax
                        raise RenderError("Subtitle font directory contains unsupported FFmpeg characters")
                    subtitle_filter += ":fontsdir=" + font_dir
                    styles.insert(0, 'FontName=' + family)
                subtitle_filter += ":force_style='" + ','.join(styles) + "'"
                vf.append(subtitle_filter)
            vf.append(
                'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,' 
                'setparams=color_primaries=bt709:color_trc=bt709:'
                'colorspace=bt709:range=limited'
            )
            graph=[]
            for i, _ in enumerate(segments):
                graph.append(f'[{i}:v]setpts=N/({fps.numerator}/{fps.denominator}*TB),setsar=1[v{i}]')
            graph.append(''.join(f'[v{i}]' for i in range(len(segments))) +
                         f'concat=n={len(segments)}:v=1:a=0[joined]')
            graph.append('[joined]settb=AVTB,setpts=N/('+str(fps.numerator)+'/'+str(fps.denominator)+'*TB),' + ','.join(vf) + '[out]')
            script=checkpoint_dir/'assemble.ffgraph'
            script.write_text(';'.join(graph),encoding='utf-8')
            try:
                cmd=['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y']
                for segment in segments:
                    cmd.extend(['-i',str(segment)])
                cmd.extend(complex_script_args(script, check_ffmpeg(project)) + ['-map','[out]',
                     '-an','-r',f'{fps.numerator}/{fps.denominator}','-fps_mode','cfr',
                     '-frames:v',str(timeline['total_frames']),'-c:v','libx264',
                     '-preset',str(cfg['output']['encoder_preset']),'-crf',str(_crf(cfg)),
                     '-profile:v','high','-pix_fmt','yuv420p',
                     '-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv',
                     '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',
                     # Ensure the coded H.264 VUI also reports BT.709.
                     # This is metadata normalization, not pixel color conversion.
                     '-bsf:v','h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1',
                     '-movflags','+faststart',str(staged)])
                _exec(cmd,timeout=2400,cwd=project.folder)
                video_info=verify_video(staged,timeline['total_frames'],w,h,fps,final=True)
                os.replace(staged,result_file)
            finally:
                staged.unlink(missing_ok=True)
        state['output']={'file':_checkpoint_path(project,result_file),'sha256':_sha256_file(result_file),'status':'PASS'}
        state['status']='PASS'
        _checkpoint_write(checkpoint_file,state,'complete')
    except BaseException as exc:
        state['status']='INTERRUPTED'
        state['error']=str(exc)
        _checkpoint_write(checkpoint_file,state,'interrupted')
        raise

    report_out={'phase':'1.7','status':'PASS','project_id':project.project_id,
                'output':str(result_file),'video':video_info,'expected_frames':timeline['total_frames'],
                'scenes':len(tracks),'cached_scenes':reuse,'cached_segments':reused_segments,'cached_output':reused_output,
                'checkpoint':str(checkpoint_file),'audio':'deferred_to_phase_1.6',
                'transitions':[x['transition_out'] for x in tracks],
                'motion_engine':'Phase 0.2 OpenCV Lanczos4 affine subpixel',
                'warnings':timeline.get('warnings',[])}
    atomic_write_json(project.folder/'reports'/'render_report.json',report_out)
    return report_out
