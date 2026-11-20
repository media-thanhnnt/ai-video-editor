"""Phase 1.9 helpers: exact numbered-frame validation and FFmpeg frame sequence encoding."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

from .project import ValidationError


def fit_filter(width: int, height: int, mode: str) -> str:
    if mode == 'cover':
        return f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}'
    if mode == 'contain':
        return f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2'
    if mode == 'blur_fill':
        # Sequence scenes do not use OpenCV's blur-fill background; reject rather than misrender.
        raise ValidationError('image_sequence does not support blur_fill; choose cover or contain')
    raise ValidationError(f'Invalid image fit mode: {mode}')


def sequence_manifest(scene: dict[str, Any]) -> dict[str, Any]:
    from .assets import sequence_frame_paths
    if scene.get('type') != 'image_sequence':
        raise ValidationError('Expected image_sequence scene')
    return {'scene_id':scene['scene_id'],'frames':sequence_frame_paths(scene),
            'frame_count':scene['sequence']['frame_count'], 'fps':scene['sequence']['fps']}
