# Phase 1.8 — End-to-End Integration & Quality Assurance

## Scope

- New CLI: `ai-video-editor qa PROJECT_ID [--deep]`.
- Validate project assets, editing plan, freshness of saved `timeline.json`, actual `video_only.mp4` and `final.mp4`.
- Verify H.264 / 1080p-or-configured resolution / FPS / exact video frame count / BT.709 metadata.
- Verify AAC-LC, sample rate, channels, decoded sample count with AAC frame tolerance (existing Phase 1.6 inspector).
- Write `reports/integration_report.json` atomically, including individual PASS/FAIL/BLOCKED checks, warnings, summary, and time.
- Exit status 0 on all checks PASS, 1 if any validation fails.
- Run strictly read-only w.r.t. assets, editing plan, timeline, render caches and output videos; report alone is written.

## Usage

```powershell
ai-video-editor qa youtube_video_001
ai-video-editor qa youtube_video_001 --deep
```

## Deep mode (advisory)

- Decode final video via FFmpeg blackdetect; flag near-black spans lasting >=1 second.
- Decode final audio via FFmpeg silencedetect; flag silence of >=0.65s intersecting a voice interval for >=0.65s.
- Deep-scan results are **warnings**, not FAILs. Intentionally dark deep-sea footage and natural pauses are expected.
- The heuristic cannot prove that voice content matches narration semantically or the correct image is used; perform a manual watch/listen pass.
- No automatic video repair, AI evaluation, subtitle checking or background music checking (Phase 3).

## Existing Phase 1.7 behavior

Does not alter render/checkpoint or audio engine, and includes the Windows RenderLock hotfix. A resume operation is not triggered by QA, and the check does not clear any cache.

## Failure behavior

If plan or assets are invalid, timeline and output checks show BLOCKED. If media is missing, corrupt, or has incorrect streams/frames, corresponding check FAILs but other independent checks continue. JSON report is still emitted.
