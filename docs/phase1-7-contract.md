# Phase 1.7 — Checkpoint & Recovery (Implementation Contract)

## Goal

Resume rendering after interruption while reusing verified existing work. Preserve Phase 1.5's motion engine, H.264 BT.709 output, and Phase 1.6 audio behavior.

## Storage

- `work/scenes/<scene_id>_<old-cache-key>.mkv`: existing Phase 1.5 per-scene cache, backward compatible.
- `work/checkpoints/<render-key>/<index>_body.mkv`: completed scene bodies.
- `work/checkpoints/<render-key>/<index>_transition.mkv`: completed transitions.
- `work/checkpoints/<render-key>/assemble.ffgraph`: FFmpeg 9-compatible assembly filtergraph.
- `reports/render_checkpoint.json`: atomic state manifest recording render key, stages, SHA-256 of segments and final output, and last step.
- `reports/render_report.json`: backward-compatible PASS report plus cached segment/output counts.

## Cache rules

1. Validate storyboard, asset hashes, plan and timeline before touching work.
2. Generate a render identity from plan, timeline/input hashes, supersampling, dimensions, FPS, fit, encoder preset and engine/checkpoint versions.
3. Verify scene clips against frame count, FPS and resolution. **Unchanged Phase 1.5 scene keys remain reusable**.
4. Reuse a segment only when the manifest refers to the current render identity, its hash matches the file, and FFprobe verifies frame count/FPS/size.
5. If any segment fails these checks, render that segment again. Incomplete `.partial.mkv` files are never reused.
6. Reuse output only with matching render identity, SHA-256 and H.264/yuv420p/BT.709/frame checks.
7. Write final output as `video_only.partial.mp4`, verify, then atomically replace `video_only.mp4`.
8. With `--force`, regenerate all scenes, segments and final MP4 and update the manifest.
9. `--resume` is explicit syntax; validated recovery is already on by default.
10. A render lock prevents two renders of the same project on the same local machine. It can recover from dead PIDs on the same machine; sharing the same project across hosts requires external orchestration.

## Failure semantics

- Ctrl+C or error: checkpoint gets status `INTERRUPTED` when Python handles the exception; completed clip/segment files and previous final MP4 stay untouched.
- Hard crash: manifest remains at last durable atomic save. The next run verifies or rebuilds; orphan `.partial.*` is not trusted.
- Changed plan/assets/timing: new render identity. Old segments are not reused under the new identity, while individually unchanged scene cache clips may be reused.
- A deleted or corrupted segment is regenerated without re-rendering all scenes.
- The saved checkpoints consume disk space. Clean them manually only when no render is running, e.g. remove `work/checkpoints` (this does not delete your images, voice files, or final video).

## CLI

```powershell
ai-video-editor render youtube_video_001
ai-video-editor render youtube_video_001 --resume
ai-video-editor render youtube_video_001 --force
Get-Content projects/youtube_video_001/reports/render_checkpoint.json
```

**Windows target:** Python 3.14, FFmpeg 9.x `-/filter_complex`, PowerShell. **Validation in this package:** Linux FFmpeg 7.1.5 smoke tests, original 75 tests plus new checkpoint tests; user should verify on Windows using the existing project.
