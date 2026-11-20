# AI Video Editor Core — Phase 1.10.3 Release Notes

**Status:** Implemented and regression-tested on Linux; **Windows 11 / FFmpeg 9 acceptance pending**. Based on the user-supplied `AI Video Editor Core V1.zip` source (1.10.2), not a reconstruction.

## Changes

1. **Sample-accurate mixing:** Added `latency=1` to `alimiter` in the FFmpeg audio filter graph. Click impulse test at 48,000 Hz verifies a click at sample 48,000 remains at 48,000 after limiter when output is decoded as PCM. This does **not** assert AAC end-to-end precision under every decoder/priming configuration.
2. **Image motion cache revision:** Versioned still-image scene cache using `IMAGE_MOTION_CACHE_VERSION='image-motion-1.10.3'` without changing sequence or video cache keys. Render identity includes this new version when still-image scenes exist. Existing still-image scenes may be rendered again once to apply corrected Pan.
3. **Plan cache scope:** Planner cache key now depends on visual scene references, planner style/seed, and transition configuration, not unrelated approval or timeline-padding configuration. NOTE: pre-upgrade 1.10.2 editing-plan keys can be marked stale once under the new scheme. To retain manually edited choices, use `ai-video-editor approve-plan PROJECT` once; do not use `compose --replan` unless you intend to replace the plan. Changing padding still requires rebuilding the timeline but not choosing a new plan.
4. **Custom subtitle fonts:** The subtitle `fontsdir` now uses the actual configured `font_file` parent, relative to project root. Unsupported special characters in the parent path raise an explicit error rather than silently using a wrong font. Fonts outside `assets/fonts` are accepted in normal safe paths.
5. **FFmpeg min version:** Parse major and minor. FFmpeg 6.0 is rejected; FFmpeg 6.1+ passes version preflight. `-/filter_complex` is selected for FFmpeg 7+, legacy option for FFmpeg 6.1.
6. **Portable checkpoints:** Newly written scene/segment/output checkpoint file paths are relative to the project root. Legacy absolute output references are accepted in the *expected output slot* if identity and SHA-256 match. The full project directory must be moved together, including work/checkpoints and output files. Render report display paths remain absolute for user convenience.
7. Version updated to `1.10.3`, compose report and audio report phase labels updated.

## Testing performed

- 109 existing tests PASS across 3 groups (86 + 15 + 8).
- 9 new regression tests PASS.
- Total: **118 / 118** tests PASS across those groups on Linux, including FFmpeg integration.
- Windows / FFmpeg 9 not executed in this runtime. Verify on the user's machine.

## Install / verify on Windows PowerShell

```powershell
# Backup your current source / repository first. Extract the archive into the repo root.
# Keep your existing projects/ directory.
py -m pip install -e .
py -m unittest discover -s tests -v
ai-video-editor approve-plan mixed_media_demo
ai-video-editor compose mixed_media_demo --deep
```

`approve-plan` is only required for older plans reported stale after the plan-cache-key change, or where approval is required. If your plan is already current, skip it.

## Acceptance checks

- Verify audio starts at the expected sample/video position and retains the input audio.
- Verify Pan subtleties update on old projects without `--force`.
- Verify subtitle font when configured outside assets/fonts.
- Confirm no unexpected `cached final video` for projects with modified Pan logic on their first 1.10.3 run.
- Confirm subsequent runs reuse cache.
- Confirm `qa --deep` and `compose` PASS on Windows FFmpeg 9.
