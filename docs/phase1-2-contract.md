# Phase 1.2 — Asset Manager Contract

## Inputs
- Existing Phase 1.1 `Project` with a structurally valid `config.json` / `storyboard.json`.
- `voice_mode=per_scene`; `scenes[]` each has a project-relative image under `assets/images/` and audio under `assets/audio/`.
- File extensions: `.jpg`, `.jpeg`, `.png` and `.mp3`, `.wav` (case-insensitive).
- `ffmpeg` and `ffprobe` on PATH, Pillow installed.

## Outputs
- `reports/assets_report.json`, written atomically after inspecting all referenced media.
- `status=PASS` only if at least one scene, all referenced files decode, and free space clears the preliminary heuristic.
- Failure details per referenced file and scene without silently skipping any scene.
- SHA-256 hashes for referenced media and input JSON, `decoded_samples` at project audio sample rate, EXIF orientation and corrected image dimensions.

## Limits and decisions
- `get_audio_info()` uses ffprobe for metadata and ffmpeg for actual decoded/resampled PCM sample count. Intermediate PCM is temporarily written in OS temp storage, then removed. No actual media is included in report.
- Some MP3s have encoder delay and padding; decoded PCM counts can include it depending on stream metadata and decoder. Phase 1.3 must use the recorded sample counts and explicit resampling rules.
- Support for WAV is intentionally restricted to common uncompressed PCM formats; non-PCM WAV will fail visibly.
- Images are fully decoded by Pillow; high-pixel-count inputs (>120MP) are rejected.
- Files resolving outside the project root (including symlink targets) are rejected. This protects against accidental traversal, not an actively hostile process that can change files concurrently.
- Only storyboard-referenced media are validated; extra files are listed by `scan_assets()` but are not required to pass.
- Disk estimate `max(256MiB, referenced media bytes * 3)` is provisional. Phase 1.5 should replace it with an estimate for uncompressed intermediate video and target bitrate.
- Does not inspect actual audio content or verify spoken narration matches storyboard text.
- No rendering, AI tools, timeline, persistent queue, checkpoint mutations or voice synchronization in this phase.
