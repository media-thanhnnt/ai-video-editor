"""FFmpeg runtime compatibility and early capability preflight."""
import re
import subprocess
from pathlib import Path

from .project import ValidationError


def check_ffmpeg(project=None) -> int:
    """Return FFmpeg major version; refuse missing burn-in support before rendering."""
    try:
        version = subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,
                                 encoding='utf-8', errors='replace',timeout=20,check=True).stdout
        match = re.search(r'ffmpeg version\s+(?:n)?(\d+)\.(\d+)(?:\.|[-+\s]|$)', version)
        if not match:
            raise ValidationError('Cannot identify FFmpeg version; require FFmpeg 6.1 or newer')
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) < (6, 1):
            raise ValidationError('FFmpeg 6.1 or newer is required')
        if project is not None and project.config['subtitles']['mode'] in ('both','burn_in'):
            filters = subprocess.run(['ffmpeg','-hide_banner','-filters'],capture_output=True,
                                     text=True,encoding='utf-8',errors='replace',timeout=20,check=True)
            if not re.search(r'^\s*[.A-Z|]+\s+subtitles\s',filters.stdout,re.M):
                raise ValidationError('Burn-in subtitles require FFmpeg built with libass (subtitles filter)')
        return major
    except (OSError, subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise ValidationError(f'FFmpeg preflight failed: {exc}') from exc


def complex_script_args(script: Path, major: int) -> list[str]:
    return ['-/filter_complex',str(script)] if major >= 7 else ['-filter_complex_script',str(script)]
