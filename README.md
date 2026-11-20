# AI Video Editor Core — Phase 1.10.2

Phase 1.10.2 is a correctness, media-compatibility and render-cache update to the accepted Phase 1.10.1 engine. It supports `image` (Zoom/Pan), `image_sequence`, and `video_clip` sources, while mixing narration, video-embedded audio, background tracks, and SRT or burned-in subtitles.

## Upgrade an existing Windows project

Back up your source and projects first. Extract this release over source folders (`src`, `tests`, `config`, `docs`, `examples`, `scripts`) and `pyproject.toml`. **Do not delete or overwrite your `projects` folder.**

```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
```

**Approval migration:** Existing configs commonly set `planning.require_plan_approval=true`. Phase 1.10.2 enforces this before `render`, `audio` and `compose`. To preserve and explicitly reapprove your previously curated plan (including custom style, scene motions, transitions), run:

```powershell
ai-video-editor approve-plan mixed_media_demo
ai-video-editor compose mixed_media_demo --deep
```

If no editing plan exists, create one first:

```powershell
ai-video-editor plan mixed_media_demo --style documentary --seed 12345
ai-video-editor approve-plan mixed_media_demo
ai-video-editor compose mixed_media_demo --deep
```

`compose` keeps a current, approved editing plan unchanged. For an intentional replacement, run `ai-video-editor compose mixed_media_demo --replan --style calm --seed 99` and then **approve the new plan and rerun compose**. A change to only an audio track or sidecar subtitle no longer forces video encoding. Burn-in subtitle changes require a new final video encode.

Video-only cache identity changed to include corrected colorspace and burn-in behavior; the first upgraded compose may re-render. Standalone `audio` and `qa` reject stale or mismatched render checkpoints.

See [Phase 1.10.2 release notes](RELEASE_NOTES_1.10.2.md) for fixes, regressions and caveats.

---

## Historical Phase 1.9 instructions

# AI Video Editor — Phase 1.9 (Image Sequence Engine)

**New:** `image_sequence` scenes render a numbered series of PNG/JPG frames as animation and mix them with existing still-image scenes. A 90-frame sequence at 30 FPS becomes **exactly 3 seconds** of video. **No AI motion synthesis or interpolation** is performed.

## Update from Phase 1.8 on Windows

1. Back up your existing `projects` directory. Keep all your media, cached clips, and outputs.
2. Extract this ZIP and copy `src`, `tests`, `docs`, `examples`, and `pyproject.toml` into your current repository. Do **not** overwrite `projects`.
3. From the project root, run `py -m pip install -e .` then `py -m unittest discover -s tests -v`.
4. The original `youtube_video_001` still uses still-image scene schema; no migrations are required.

## New image-sequence example

Copy `examples/storyboard_image_sequence.json` into `projects/ball_throw_demo/storyboard.json` after initializing with `ai-video-editor init ball_throw_demo --title "Ném bóng"`.

Create folder `projects/ball_throw_demo/assets/sequences/throw_ball/` and put 90 identically sized numbered images inside:

```text
frame_0001.png
frame_0002.png
...
frame_0090.png
```

Run:

```powershell
ai-video-editor validate ball_throw_demo
ai-video-editor assets ball_throw_demo
ai-video-editor plan ball_throw_demo --style documentary --seed 12345
ai-video-editor timeline ball_throw_demo --use-plan
ai-video-editor render ball_throw_demo --resume
ai-video-editor audio ball_throw_demo
ai-video-editor qa ball_throw_demo --deep
```

Outputs: `projects/ball_throw_demo/output/video_only.mp4` and `final.mp4`. The latter has silent AAC if no voice was supplied. To mix sequence and still-image scenes, add both kinds into the same storyboard using consecutive `order` fields; still scenes require audio, sequence scenes may omit it.

**Compatibility:** Project output FPS must equal `sequence.fps`. Images cannot be missing or have mismatched dimensions. Use `image.fit_mode = cover` or `contain` (not `blur_fill`) for sequence projects. See [Phase 1.9 contract](docs/phase1-9-contract.md) for exact rules.

**Tested:** 94 Python unit/integration tests PASS on Linux, including real FFmpeg sequence rendering, full cache reuse, silent AAC mux and mixed still + sequence rendering. **Windows 11 / FFmpeg 9 acceptance is pending.**

---

# AI Video Editor — Phase 1.8 (Integration QA)

New read-only QA CLI validates the full project and actual MP4 media, writes `reports/integration_report.json`. Includes Phase 1.7 Windows RenderLock fix.

```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
ai-video-editor qa youtube_video_001
ai-video-editor qa youtube_video_001 --deep
```

- Default QA validates assets, plan, timeline freshness, video-only and final H.264/AAC files. It **does not render again**.
- `--deep` additionally flags possible black-video and silence-during-voice intervals; these are warnings, not automatic errors.
- Check result: `reports/integration_report.json`; exit code `0` = PASS, `1` = FAIL.
- Requires `final.mp4` produced by `ai-video-editor audio PROJECT`.
- Keep `projects` untouched when installing the updated source.
- Full contract: `docs/phase1-8-contract.md`.

---

# AI Video Editor — Phase 1.7 (Checkpoint & Recovery)

**New features:** resumable scene + body/transition cache, durable progress manifest, verified reuse, interrupted-run recovery, render lock, and atomic final MP4. Preserves all Phase 1.1–1.6 features and FFmpeg 9 `-/filter_complex` compatibility. Phase 1.6 audio is unchanged.

## Upgrade existing Windows project (IMPORTANT)

1. Make a backup of your project folder, especially `projects/youtube_video_001`.
2. Extract this archive to a new folder; it contains complete sources. Your original project assets are **not** bundled.
3. Copy your entire original `projects/youtube_video_001` into the extracted new folder's `projects` directory, **or** copy `src`, `config`, `docs`, `scripts`, `tests`, `pyproject.toml` into the existing repository, preserving the original `projects` directory.
4. Open PowerShell in the root folder and install: `py -m pip install -e .`
5. Run tests: `py -m unittest discover -s tests -v`
6. Render with `ai-video-editor render youtube_video_001 --resume`.

> `--resume` is optional. The normal `render` command already resumes, except when `--force` is used. First 1.7 run reuses already existing Phase 1.5 scene clips, but **builds the new persistent body/transition checkpoints once**. From the second 1.7 run, all steps can be reused.

## Outputs

- `reports/render_checkpoint.json` — per-step checkpoint manifest, current render key, status and hashes.
- `work/scenes/` — already existing scene clips (reused).
- `work/checkpoints/<render-key>/` — persistent body and transition clips; do not delete during render.
- `output/video_only.mp4` — protected MP4, never overwritten until staged output verifies.
- `reports/render_report.json` — includes `cached_scenes`, `cached_segments`, and `cached_output`.

## Commands

```powershell
ai-video-editor render youtube_video_001 --resume
ai-video-editor audio youtube_video_001
Get-Content projects/youtube_video_001/reports/render_checkpoint.json
```

Render from scratch (ignores cache):

```powershell
ai-video-editor render youtube_video_001 --force
```

## Verification

```powershell
py -m unittest discover -s tests -v
ffprobe -v error -count_frames -show_entries stream=codec_name,nb_read_frames,avg_frame_rate,width,height -of json "projects/youtube_video_001/output/video_only.mp4"
```

Read [Phase 1.7 Contract](docs/phase1-7-contract.md) for cache invalidation, interruption recovery, limitations and file layout.

## Previous feature notes

# AI Video Editor — Phase 1.6 (Voice Engine)

**Status:** Implemented, Linux smoke-tested, **pending acceptance on Windows 11 / FFmpeg 9.0.2**.

Includes source and tests for the previously accepted Phase 1.1–1.5 features. Phase 1.5 includes the FFmpeg 9 compatible `-/filter_complex` hotfix.

## New in Phase 1.6

- Build sample-aligned PCM voice track from MP3/WAV per-scene narration.
- Add precise silence before, between and after narration segments according to `timeline.json`.
- Mux AAC-LC stereo into the already-rendered video, avoiding another image render.
- Validate both MP4 streams and record `reports/audio_report.json`.
- Detect missing media, stale timeline, mismatching decoded voice duration and invalid video-only input.
- Atomic final MP4 output: intermediate `final.partial.mp4` only renamed on successful validation.

**Deferred to Phase 3:** background music, music ducking, two-pass LUFS normalization, subtitles and `single_track` narration with alignment.

## Install on Windows

Prerequisites: Python >= 3.10, FFmpeg + FFprobe in PATH.

Open PowerShell in this extracted folder:

```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
```

Expected: `Ran 75 tests` and `OK` (assuming required FFmpeg support for H.264 and MP3).

## Test with a new project

```powershell
py scripts/create_demo_assets.py --project-id phase16_demo
ai-video-editor assets phase16_demo
ai-video-editor plan phase16_demo --style documentary --seed 12345
ai-video-editor timeline phase16_demo --use-plan
ai-video-editor render phase16_demo
ai-video-editor audio phase16_demo
```

Artifacts:

```text
projects/phase16_demo/
├── output/
│   ├── video_only.mp4       # Phase 1.5: video-only (reusable)
│   └── final.mp4            # Phase 1.6: H.264 + AAC voice
├── work/audio/voice_mix.wav # PCM aligned to timeline
└── reports/audio_report.json
```

The generated voices are synthetic tones for testing. With real narration, provide one WAV/MP3 file per scene as defined in the storyboard.

**Important:** `audio` will not re-render video. In an existing Phase 1.5 project with correct `video_only.mp4`, simply run:

```powershell
ai-video-editor audio phase15_demo
```

This requires the same project under `projects/`, unchanged input assets, `timeline.json` and `editing_plan.json`.

## Validate final video and audio

```powershell
ffprobe -v error -count_frames -show_entries stream=codec_type,codec_name,profile,pix_fmt,width,height,nb_read_frames,sample_rate,channels,color_space -of json "projects/phase16_demo/output/final.mp4"
Get-Content "projects/phase16_demo/reports/audio_report.json"
```

Expected for the demo with documentary seed 12345: 10 scenes; **516 video frames**, 30 FPS, audio sample rate 48000 Hz, 2 channels and a single AAC-LC track. The report has `final_qa.aac_sample_difference` (may be nonzero due to AAC padding).

## Validate in Windows Media Player

1. Open `projects/phase16_demo/output/final.mp4`.
2. Confirm video is visible, and you can hear all ten short tones in order.
3. Confirm the video continues throughout and has no unexpected sound gaps other than the timeline's intentional pauses.
4. Confirm `output/video_only.mp4` is unchanged, and `final.mp4` contains one audio track.

## Troubleshooting

- **`Video missing`:** run `ai-video-editor render phase16_demo` first.
- **`Stale timeline`:** rebuild with `ai-video-editor timeline phase16_demo --use-plan`, then rerender video if affected.
- **`decoded ... samples, timeline expects ...`:** run `ai-video-editor assets` and rebuild timeline. Source MP3 may have decode/timing variations; the engine refuses to clip real narration silently.
- **Windows codec issue:** confirm H.264 yuv420p BT.709 and AAC-LC with FFprobe.
- **Voice sounds too quiet/loud:** loudness processing is scheduled for Phase 3; audio levels follow the supplied files.

## Unit tests

`py -m unittest discover -s tests -v`

See [Phase 1.6 Contract](docs/phase1-6-contract.md).

---

## Phase 1.10: Mixed Media, additional audio and subtitles

AI Video Editor now accepts three scene types in one storyboard: `image` (Zoom/Pan), `image_sequence` (animation from sequential frames) and `video_clip` (existing MP4/MOV/MKV).

**Single-command workflow** (once files have been placed into the project):

```powershell
ai-video-editor compose mixed_media_demo --deep
```

Or execute `assets`, `plan`, `timeline --use-plan`, `render --resume`, `audio` and `qa --deep` separately as in earlier phases.

See `examples/storyboard_mixed_media.json` and `docs/phase1-10-contract.md` for complete input schema and migration steps. Additional global `audio_tracks` use absolute timeline start times; subtitle `mode` in `config.json` is `off`, `sidecar`, `burn_in`, or `both`. Original embedded sound in `video_clip` is intentionally omitted unless you supply it as a separate audio track.

## Phase 1.10.1 — Audio embedded in video scenes

Video clips with embedded audio now retain that audio by default, exactly aligned to
`timeline.video_track[].start_frame` and linearly crossfaded over dissolve overlaps.
No separate extraction or manual `start_seconds` is needed. Example:

```json
{
  "scene_id": "003", "order": 3, "type": "video_clip",
  "video": "assets/videos/scene_003.mp4", "narration": "",
  "source_audio": {"enabled": true, "volume": 1.0}
}
```

The `source_audio` object is optional: default is to use embedded audio if present.
Set `enabled: false` to mute a clip; explicitly `true` requires an audio stream.
**Remove manually added `scene_003.wav` audio tracks to avoid doubling the sound.**
Run `ai-video-editor compose mixed_media_demo --deep` again to rebuild the final
MP4, and see `reports/audio_report.json` → `source_video_audio` for sample offsets
and fade lengths. Full contract: `docs/phase1-10-1-contract.md`.


## Phase 1.10.3 — Precision & Cache Correctness

See [release notes](RELEASE_NOTES_1.10.3.md) for implementation details, regression tests, and Windows upgrade instructions.
