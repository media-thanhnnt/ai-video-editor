# Phase 1.5 — Video Render Engine Contract

## Input
- An existing Phase 1 project containing validated `editing_plan.json` and `timeline.json` generated with `--use-plan`.
- At least one validated image for each scene. Audio files must also exist so Asset Manager can verify the timeline, but **audio is not muxed** in this phase.
- `config.json` (resolution, FPS, fit mode, supersampling, encoding preset).

## Output
- `output/video_only.mp4`: H.264 High, yuv420p, BT.709, CFR, MP4 faststart, **no audio**.
- `reports/render_report.json`: verification summary, transition definitions, scene cache hit count, warnings.
- `work/scenes/*.mkv`: per-scene FFV1 lossless clips, reusable by hash.
- `work/segments/`: temporary assembled segments (cleaned after rendering).

## Guarantees
- FPS, output dimensions and total frame count are validated with FFprobe.
- Motion for every frame is calculated from its absolute frame index (Phase 0.2 OpenCV Lanczos4 approach), never from accumulated motion.
- Each transition is rendered separately and occupies exactly its designated overlap frames.
- Intro and outro fades do not affect total frame count.
- Timeline and media hashes are revalidated before rendering. No source asset or storyboard is modified.
- Output is first written to `.partial.mp4` then atomically renamed after verification.

## Constraints / known limitations
- Phase 1.5 produces video-only output; speech, music, subtitles and sample-accurate muxing belong to Phase 1.6 and later.
- Only one renderer runs per project. Concurrent processes may conflict on `work` paths.
- Source image color handling does not implement full ICC profile management. BT.709 is signaled on encoded output.
- Intermediate FFV1 may consume significant disk space; no full job-state recovery until Phase 1.7.
- Some formats or very long project timelines may exceed FFmpeg input or resource limits; Phase 1.7 will add bounded batch assembly.
- Windows visual assessment is still required to accept motion quality at the target output resolution.
