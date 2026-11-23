"""Phase 1.9 helpers: exact numbered-frame validation and FFmpeg frame sequence encoding."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

from .project import ValidationError


def fit_filter(width: int, height: int, mode: str) -> str:
    # 'auto' decides per still image (see renderer._resolve_fit); video and sequence
    # frames keep the historical crop-to-fill behaviour.
    if mode == 'auto':
        mode = 'cover'
    if mode == 'blur_fill':
        # Same look as the still-image blur fill: blurred cover background + contained foreground.
        return (f'split=2[ave_bg][ave_fg];'
                f'[ave_bg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,'
                f'crop={width}:{height},gblur=sigma=24[ave_blur];'
                f'[ave_fg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[ave_front];'
                f'[ave_blur][ave_front]overlay=(W-w)/2:(H-h)/2')
    if mode == 'cover':
        return f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}'
    if mode == 'contain':
        return f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2'
    raise ValidationError(f'Invalid image fit mode: {mode}')


def sequence_manifest(scene: dict[str, Any]) -> dict[str, Any]:
    from .assets import sequence_frame_paths
    if scene.get('type') != 'image_sequence':
        raise ValidationError('Expected image_sequence scene')
    return {'scene_id':scene['scene_id'],'frames':sequence_frame_paths(scene),
            'frame_count':scene['sequence']['frame_count'], 'fps':scene['sequence']['fps']}
