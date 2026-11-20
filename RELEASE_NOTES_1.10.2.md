# Phase 1.10.2 — Correctness and rendering hotfix

This update is based on accepted Phase 1.10.1. It changes cache/checkpoint identity; an existing project needs one new render before running `audio` or `qa` separately. `compose` handles this automatically.

## Fixes
- Video stream duration rather than container duration (audio tails no longer inflate target frames); FFmpeg output `fps,tpad,scale` ensures expected frame count.
- Sequence with narration no longer mutates frame references.
- BT.709 RGB->YUV conversion now occurs before setting metadata; regression tests measure decoded RGB.
- FFmpeg version/capability preflight; FFmpeg 6 uses `-filter_complex_script`, FFmpeg 7+ uses `-/filter_complex`.
- Checkpoint freshness and SHA-256 validation before `audio` / QA.
- `compose` preserves a valid plan, with `--replan`, `--style`, `--seed` options.
- Silent sequence/video shorter than regular voice padding supported.
- Audio and sidecar subtitle changes no longer invalidate visual render caching; burn-in changes intentionally invalidate the final video.
- Mixed audio sample-addressed offsets and configurable peak limiter.
- Subtitle burn-in moved to renderer so video is H.264-encoded once; audio mux uses stream copy.
- Pan intensity now changes travel distance, updated package version and source layout.
- Imported SRT timing validation.

## Usage
```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
ai-video-editor approve-plan mixed_media_demo
ai-video-editor compose mixed_media_demo --deep
```

When `planning.require_plan_approval=true`, the existing plan must be explicitly approved using `ai-video-editor approve-plan PROJECT`. This also migrates an older plan's cache key while preserving manual scene choices. `compose` never silently overwrites a stale approved plan. To replace a plan intentionally:
```powershell
ai-video-editor compose mixed_media_demo --replan --style documentary --seed 42 --deep
# This creates an unapproved replacement plan and stops before rendering.
ai-video-editor approve-plan mixed_media_demo
ai-video-editor compose mixed_media_demo --deep
```

## Limitations
- Tested on local FFmpeg 7/Linux; Windows 11 + FFmpeg 9 needs user acceptance.
- Accurate color management for tagged input HDR or unusual matrices requires further metadata-aware conversion; RGB fidelity regression covers standard SDR source.
- Peak limiter bounds the pre-AAC mixed PCM sample peak; a strict *true peak* guarantee needs oversampled measurement + loudness pass.
- SHA-256 rehashing on each validation is retained intentionally for strong tamper/staleness checks.
- Video-backed MP4 H.264 cache still reuses scenes but re-encodes the merged video after visual changes.
