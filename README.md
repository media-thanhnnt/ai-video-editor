# Latest hotfix: Phase 2.5.1.3

For provider API key isolation, Editing Plan protection, and review fixes, see **[README_PHASE2_5_1_3_REVIEW_FIX.md](docs/changelog/README_PHASE2_5_1_3_REVIEW_FIX.md)**.

# AI Video Editor

A local, deterministic video editor that turns a storyboard of still images, image sequences and video clips plus per-scene narration into a finished H.264/AAC MP4. On top of the rendering core sits an optional, human-in-the-loop AI layer (script → scene plan → assets), a CLI for all of it, and a localhost web GUI.

| Layer | Version | What it does |
|---|---|---|
| **Core** | 1.10.3 | Frame/sample-accurate timeline, Zoom/Pan motion, transitions, narration + background + source-video audio mix, SRT or burned-in subtitles, resumable render cache, QA |
| **Agent** | 2.1–2.3 | Generate or import a script, plan scenes with AI, bind media, compile a Core storyboard — every step needs explicit approval |
| **GUI** | 2.4.x | Dashboard, Script Editor, Scene Planner, Asset Workspace, Render Studio, API Settings, Help |
| **CLI parity** | 2.5.1 | Provider routing, image/voice generation, batches, voice selection and auto-binding without the web server |

Nothing is sent to a paid API unless you explicitly trigger it. Rendering always runs locally with FFmpeg.

---

## Contents

1. [Requirements and installation](#1-requirements-and-installation)
2. [Quick start](#2-quick-start)
3. [Project folder layout](#3-project-folder-layout)
4. [Core pipeline](#4-core-pipeline)
5. [AI agent workflow (CLI)](#5-ai-agent-workflow-cli)
6. [Providers, API keys and asset generation](#6-providers-api-keys-and-asset-generation)
7. [Web GUI](#7-web-gui)
8. [CLI reference](#8-cli-reference)
9. [HTTP API reference](#9-http-api-reference)
10. [Security model](#10-security-model)
11. [Testing](#11-testing)
12. [Troubleshooting](#12-troubleshooting)
13. [Known limitations](#13-known-limitations)
14. [Upgrading an existing installation](#14-upgrading-an-existing-installation)
15. [Technical specifications per phase](#15-technical-specifications-per-phase)
16. [Changelog](#16-changelog)

---

## 1. Requirements and installation

- Python ≥ 3.10 (Windows target: Python 3.14)
- FFmpeg + FFprobe ≥ 6.1 on `PATH`. A libass build is needed only for burned-in subtitles.
- Optional: OpenAI, Google Gemini or another OpenAI-compatible provider account for AI features.

```powershell
py -m pip install -e .            # Core + CLI (no web server)
py -m pip install -e ".[ui]"      # + web GUI: FastAPI, uvicorn, python-multipart, keyring
py -m pip install -e ".[ui,test]" # + GUI test dependencies
```

On Linux use `python -m pip install -e '.[ui]'`. The `ui` extra is also what enables storing API keys in the OS keyring from the CLI.

---

## 2. Quick start

### 2.1 CLI — render an existing storyboard

```powershell
ai-video-editor init my_video --title "My video"
# put media under projects/my_video/assets/... and fill projects/my_video/storyboard.json
ai-video-editor validate my_video
ai-video-editor plan my_video --style documentary --seed 12345
ai-video-editor approve-plan my_video
ai-video-editor compose my_video --deep
```

Output: `projects/my_video/output/final.mp4` (plus `video_only.mp4`, `subtitles.srt` and reports in `reports/`).

Demo project with synthetic media (10 scenes of test tones):

```powershell
py scripts/create_demo_assets.py --project-id demo
ai-video-editor compose demo --deep
```

Example storyboards: `examples/storyboard_2scenes.json`, `storyboard_image_sequence.json`, `storyboard_mixed_media.json`.

### 2.2 GUI — full AI-assisted workflow

```powershell
ai-video-editor ui            # http://127.0.0.1:8765  (built-in guide at /help)
```

1. **Dashboard** → create a project.
2. **API Settings** → store provider keys (OS keyring) and pick a provider/model per task.
3. **Script Editor** → generate, edit or import a script → Save → Approve → Export Brief.
4. **Scene Planner** → generate/edit the scene plan → Approve → Export Asset Brief.
5. **Asset Workspace** → copy prompts to external tools, or generate images/voices (paid, confirmed per request).
6. **Render Studio** → upload/bind media → prepare storyboard + editing plan → approve plan → render → preview.

### 2.3 CLI — full AI-assisted workflow

```powershell
ai-video-editor agent-script my_video --request "Video 2 phút về đại dương, documentary" --use-provider-settings
ai-video-editor agent-script-approve my_video
ai-video-editor agent-scene-plan my_video --configured-provider
ai-video-editor agent-scene-approve my_video
ai-video-editor agent-scene-export my_video
ai-video-editor agent-init my_video --brief .\projects\my_video\agent_scene_brief.json
ai-video-editor asset batch-image my_video --all-scenes --auto-bind
ai-video-editor asset batch-voice my_video --all-scenes --voice Kore --auto-bind
ai-video-editor agent-approve my_video
ai-video-editor agent-compile my_video
ai-video-editor agent-scene-apply my_video
ai-video-editor approve-plan my_video
ai-video-editor compose my_video --deep
```

---

## 3. Project folder layout

```text
projects/<project_id>/
├── config.json, storyboard.json, job_state.json      # Core inputs
├── editing_plan.json, timeline.json                  # Core plan and timeline
├── agent_script_draft.json                           # script draft + approval hash + token usage
├── agent_brief_generated.json                        # brief exported from the script
├── agent_scene_plan.json, agent_scene_brief.json     # scene proposal + brief exported from it
├── agent_manifest.json                               # per-scene media/voice bindings
├── generated_assets.json                             # audit log of generated files (no prices)
├── api_request_limits.json, api_request_usage.json   # local daily attempt budget
├── voice_preferences.json                            # selected TTS voice per provider
├── script_revision_pending.json                      # present while downstream steps must be redone
├── history/                                          # archived files from earlier script revisions
├── assets/{images,audio,sequences,videos,subtitles,music,fonts}/
├── work/                                             # render cache, checkpoints, render.lock
├── output/{video_only.mp4,final.mp4,subtitles.srt}
├── logs/gui_render_last.log                          # combined output of the last GUI render
└── reports/                                          # assets, render, checkpoint, audio, QA reports
```

Repository-level configuration:

- `config/default_project.json`: config used when creating a project.
- `config/providers.json`: editable provider and model catalog (no secrets).
- `.ai_video_editor_provider_settings.json`: the provider/model chosen for each task (gitignored, no secrets).

`projects/` is local data and is never committed.

---

## 4. Core pipeline

### 4.1 Steps

```text
init → validate → assets → plan → approve-plan → timeline --use-plan → render → audio → qa
```

`compose PROJECT --deep` runs assets → plan check → timeline → render → audio → QA in one command and reuses every valid cache.

| Step | Command | Output |
|---|---|---|
| Create project | `init ID --title "…"` | folder structure, `config.json`, `storyboard.json`, `job_state.json` |
| Structural check | `validate ID` | JSON and path validation only (no media probing) |
| Media check | `assets ID` | `reports/assets_report.json`: decoded sample counts, SHA-256, disk estimate |
| Editing plan | `plan ID --style … --seed …` | `editing_plan.json`: motion, intensity and transition per scene |
| Approve plan | `approve-plan ID` | sets `approved_by_user=true` and refreshes the cache key |
| Timeline | `timeline ID --use-plan` | `timeline.json` in integer frames and audio samples |
| Video render | `render ID [--force] [--supersample N]` | `output/video_only.mp4`, `reports/render_checkpoint.json` |
| Audio + subtitles | `audio ID` | `output/final.mp4`, `work/audio/voice_mix.wav`, `reports/audio_report.json` |
| QA | `qa ID [--deep]` | `reports/integration_report.json`; exit code 0 = PASS, 1 = FAIL |

### 4.2 Storyboard scene types

Every scene has `scene_id` (letters, digits or `_`; unique), `order` (must equal its 1-based array position) and `type`. All paths are project-relative POSIX paths and must resolve inside the project; symlink escapes are rejected.

**`image`: still image with Zoom/Pan.**

```json
{"scene_id":"001","order":1,"type":"image","narration":"…",
 "image":"assets/images/scene_001.png","audio":"assets/audio/scene_001.wav"}
```

Needs narration and a voice file (WAV/MP3). Images must be PNG/JPEG under 120 MP; EXIF orientation is applied.

**`image_sequence`: numbered frames played as animation.**

```json
{"scene_id":"002","order":2,"type":"image_sequence","narration":"",
 "sequence":{"frames_dir":"assets/sequences/throw_ball","frame_pattern":"frame_%04d.png",
             "frame_count":90,"fps":"30/1"}}
```

- `frames_dir` must be under `assets/sequences/`.
- `frame_pattern` must be `frame_%04d` or `frame_%05d` with `.png`, `.jpg` or `.jpeg`. Frames are numbered from 1, contiguous, at most 18,000.
- `sequence.fps` must equal the project output FPS: 90 frames at 30 FPS last exactly 3 s. Every source image becomes one output frame, with no interpolation and no Zoom/Pan (motion is forced to `static`).
- All frames must share one size. Use `fit_mode` `cover` or `contain` (`blur_fill` is not supported for sequences).
- Audio is optional. If present, the voice plus head/tail padding must fit inside the frame count; narration is never trimmed.

**`video_clip`: existing MP4/MOV/MKV under `assets/videos/`.**

```json
{"scene_id":"003","order":3,"type":"video_clip","narration":"",
 "video":"assets/videos/scene_003.mp4",
 "source_audio":{"enabled":true,"volume":1.0}}
```

- **Duration and frame rate.** The scene lasts as long as the *video stream* (not the container), quantized to the output FPS. Frames are duplicated or dropped to change FPS; nothing is interpolated.
- **Embedded audio.** It is kept by default and aligned to the scene's start frame (`start_sample = round(start_frame × sample_rate / fps)`). It is padded or cut to the scene's visual length and fades linearly over dissolve overlaps; a hard cut has no fade.
- **`source_audio` options:**
  - omit it to use the first audio stream if one exists;
  - `enabled: true` makes asset validation fail when the clip is silent;
  - `enabled: false` mutes the clip;
  - `volume` is a linear multiplier from 0 to 2.
- **Optional narration.** It needs `audio` under `assets/audio/` and must fit inside the clip together with its padding.
- **Not supported:** trim-in/out, speed changes or audio stream offsets. Trim clips before importing.
- **Migration from 1.10.** If you previously extracted a clip's audio into `audio_tracks`, remove that track to avoid hearing it twice.

### 4.3 Global audio tracks and subtitles

```json
"audio_tracks": [{"file":"assets/audio/background.wav","start_seconds":0,"volume":0.15}],
"subtitles": [{"start_seconds":0,"end_seconds":1.2,"text":"Một câu chuyện bắt đầu."}]
```

**Audio tracks**
- Files must be under `assets/audio/`. `start_seconds` is absolute timeline time and `volume` is linear 0–2, not dB.
- Tracks are cut at the video end. There is no ducking, looping or loudness mastering.

**Subtitles**
- Use either inline `subtitles` or `"subtitle_file": "assets/subtitles/captions.srt"` (UTF-8, timings validated), not both.
- There is no speech recognition or word alignment. The generated file is `output/subtitles.srt`.
- `config.json` → `subtitles.mode`:
  - `off`: SRT exported if cues exist, nothing embedded.
  - `sidecar`: external SRT, video stream copied.
  - `burn_in`: burned into the single H.264 encode (needs libass).
  - `both`: SRT and burn-in.
- **Custom font.** Set it with `subtitles.font_file`. The font's folder path must not contain `\ ' : , ; [ ]`; fonts outside `assets/fonts` are allowed.

### 4.4 Editing plan and approval

- **Rule-based plan.** `plan` builds a deterministic plan from `style` and a signed 64-bit `seed`; identical inputs give byte-identical files. The AI scene planner can create an `ai_assisted` plan instead (section 5).

  | Style | Motions | Intensity | Transitions | Length |
  |---|---|---|---|---|
  | documentary | zoom_in, pan_left, zoom_out, pan_right, static | subtle, medium | crossfade, dissolve, none | medium |
  | calm | static, zoom_in, zoom_out, pan_left, pan_right | subtle | crossfade, dissolve | long |
  | energetic | pan_left, zoom_in, pan_right, zoom_out, static | medium, strong | none, crossfade | short |

- **Plan schema.** Per scene the plan contains only `scene_id`, `motion`, `motion_intensity`, `transition_to_next` and `transition_length`. Scene IDs must match the storyboard order exactly. The last scene has `transition_to_next: none` with a `null` length, and `video_clip` / `image_sequence` scenes are always `static`.
- **Cache key.** The plan's `cache_key` covers the visual scene references, style, seed and transition configuration. Changing padding or audio does not make a plan stale. A cache key is not a signature of the plan's contents.
- **Approval gate.** With `planning.require_plan_approval=true` (the default), `render`, `audio` and `compose` refuse an unapproved plan. `approve-plan` keeps curated scene choices and also refreshes the cache key of an older plan.
- **`compose` never discards an approved plan.** It keeps a current approved plan, and if an approved plan has gone stale it stops and asks for `approve-plan` or `--replan`. To replace a plan on purpose:

  ```powershell
  ai-video-editor compose ID --replan --style calm --seed 99   # writes a new unapproved plan and stops
  ai-video-editor approve-plan ID
  ai-video-editor compose ID --deep
  ```

### 4.5 Timing model

- Frame and sample indices are authoritative; values in seconds are for display only.
- Voice duration is the decoded PCM sample count at the project sample rate (48 kHz by default). Narration is never trimmed or stretched.
- An image scene lasts `pad_head + voice + pad_tail`, and at least `min_scene_seconds`.
- Each scene starts at the previous scene's end minus the transition overlap. An overlap is at most `tail[i] + head[i+1]`, and two adjacent overlaps can never consume the scene between them.
- A transition that does not fit is clamped with a warning, or fails when `transitions.violation_policy=fail`.
- Intro and outro fades (`transitions.intro`, `outro`, `intro_outro_seconds`) never change the total frame count. An outro longer than the last tail is clamped or fails; an intro longer than the head padding produces an `INTRO_OVER_VOICE` warning.
- Every voice stays fully inside its scene without overlapping its neighbours. Because transitions overlap, the total duration can be shorter than the sum of the scenes.

### 4.6 Render cache, checkpoints and recovery

- **Motion.** Each frame is computed from its absolute frame index with an inverse affine transform and OpenCV Lanczos4 on a supersampled source (`motion.supersample_factor`, 1–4), so errors never accumulate between frames.
- **Scene clips.** Stored in `work/scenes/<scene>_<key>.mkv` (FFV1). The key covers the media hash, motion, frame count, resolution, FPS, supersample factor and fit mode. A sequence key includes every frame's hash.
- **Body and transition segments.** Stored in `work/checkpoints/<render-key>/` and reused only when the render identity, the SHA-256 recorded in `reports/render_checkpoint.json` and an FFprobe check all match. Partial files are never reused.
- **Final video.** `output/video_only.mp4` is written through `video_only.partial.mp4` and replaced only after verification.
- **Resume, force and invalidation.**
  - `render` resumes by default (`--resume` is optional) and `--force` rebuilds everything.
  - Changing only audio or sidecar subtitles never forces a video re-encode; changing burn-in subtitles does.
  - `audio` and `qa` reject a stale or mismatched render checkpoint.
- **Interruptions.** Ctrl+C or an error marks the checkpoint `INTERRUPTED` and keeps completed clips. After a hard crash, the next run verifies or rebuilds.
- **Render lock.** `work/render.lock` prevents two renders of one project on the same machine and recovers automatically from a dead PID. Projects shared across machines need external coordination.
- **Moving and cleaning up.** Checkpoint paths are project-relative, so move a whole project folder (including `work/` and `output/`) together. You may delete `work/checkpoints` when no render is running; this never deletes your media or final video.

### 4.7 Output format and audio mix

- **Video:** H.264 High, yuv420p, BT.709 signalled in both the stream and the container, constant frame rate at the configured FPS, MP4 faststart.
- **Audio:** AAC-LC at 192 kbps with the configured sample rate and channel count (mono or stereo).
- **Voice track.** Built on disk as PCM16 (`work/audio/voice_mix.wav`) with exact silence gaps. Background tracks and source-video audio are then mixed in with `amix`.
- **Limiter.** The mixed PCM is sample-peak limited by `audio.true_peak_max_dbtp`. This is not a full true-peak or LUFS mastering pass.
- **Sync tolerance.** QA accepts up to ±2048 decoded AAC samples of difference to allow for encoder priming and padding.
- **Silent projects.** A fully silent project still gets one silent AAC track.

### 4.8 QA

`qa ID` is read-only: it never re-renders or clears caches. It checks:
- assets;
- that the plan and timeline are fresh;
- the render checkpoint;
- `video_only.mp4` and `final.mp4`: codecs, frame count, sample count and colour metadata.

When the plan or the assets are invalid, the dependent checks are reported as BLOCKED, while independent checks still run. `--deep` adds warnings, never failures, for near-black video lasting ≥ 1 s and for silence ≥ 0.65 s inside expected narration.

---

## 5. AI agent workflow (CLI)

Every stage writes a reviewable JSON draft. The next stage accepts it only after explicit approval, and the approval is bound to a SHA-256 hash of the content, so editing it later requires approving again. The same steps are available in the GUI.

```text
request ──agent-script──▶ agent_script_draft.json ──approve──▶ agent_brief_generated.json
                                │
                                └─agent-scene-plan─▶ agent_scene_plan.json ──approve──▶ agent_scene_brief.json
brief ──agent-init──▶ agent_manifest.json ──bind / asset …──▶ agent-approve ──agent-compile──▶ storyboard.json
agent_scene_plan.json ──agent-scene-apply──▶ editing_plan.json (ai_assisted) ──approve-plan──▶ compose
```

### 5.1 Script (Phase 2.2)

- **Offline import (no API cost):** ask any chatbot for JSON shaped like `examples/agent_script_sample.json`, then run `agent-script ID --import-json .\script.json`.
- **OpenAI Responses API (billable):** `agent-script ID --request "…" --model gpt-4.1-mini`, or `--request-file .\request.txt` for long requests.
- **Configured provider (billable):** add `--use-provider-settings` to use the provider and model chosen for the *script* task (section 6).

```powershell
ai-video-editor agent-script-status ID      # status, scene count, provider-reported tokens
ai-video-editor agent-script-approve ID
ai-video-editor agent-script-export ID      # → agent_brief_generated.json
```

Script rules:
- **Fields:** `title`, `language`, `style` (`documentary` | `calm` | `energetic`), `target_duration_seconds` (5–7200), `summary`, `script` (the full narration text) and `scenes[]` (1–120).
- **Scenes:** each has `scene_id`, `type` (`image` | `video_clip`), `narration`, `prompt` and `estimated_duration_seconds` (0–600). Image scenes need narration.
- **Durations are estimates.** Real timing always comes from the bound voice and media files.
- **Replacing a draft** needs `--replace`. This is checked *before* any billable request.
- **Requests and usage:**
  - OpenAI requests use strict JSON Schema with `store=false`.
  - Token usage is reported by the provider.
  - `estimated_cost_usd` stays `null`, because the cost depends on your account pricing.

### 5.2 Scene plan (Phase 2.3)

Requires an approved, unchanged script.

```powershell
ai-video-editor agent-scene-plan ID --model gpt-4.1-mini        # billable (OpenAI)
ai-video-editor agent-scene-plan ID --configured-provider        # billable (scene_plan routing)
ai-video-editor agent-scene-plan ID --import-json .\examples\agent_scene_plan_sample.json   # offline
ai-video-editor agent-scene-status ID
ai-video-editor agent-scene-approve ID
ai-video-editor agent-scene-export ID      # → agent_scene_brief.json
```

**What each scene proposes:** `media_type` (`image` | `video_clip`), an English `asset_prompt_en`, `estimated_duration_seconds`, `motion`, `motion_intensity`, `transition_to_next`, `transition_length` and `reason`.

**Rules:**
- Scene IDs and order must match the script.
- `video_clip` motion must be `static`.
- The last transition must be `none` with a `null` length.
- An image scene needs narration in the script.
- Editing the script makes the scene plan stale.
- Image sequences are never proposed by AI; add them by hand in the brief.

### 5.3 Asset manifest and binding (Phase 2.1)

```powershell
ai-video-editor agent-init ID --brief .\projects\ID\agent_scene_brief.json   # or agent_brief_generated.json / examples/agent_brief_manual.json
ai-video-editor agent-status ID                                              # lists missing media/voice
ai-video-editor agent-bind ID --scene 001 --kind media --file assets/images/scene_001.png
ai-video-editor agent-bind ID --scene 001 --kind voice --file assets/audio/scene_001.wav
ai-video-editor agent-bind ID --scene 002 --kind media --file assets/videos/scene_002.mp4
ai-video-editor agent-approve ID
ai-video-editor agent-compile ID          # → storyboard.json
```

**Where files go:**
- Image media under `assets/images/` (`.png`, `.jpg`, `.jpeg`).
- Video media under `assets/videos/` (`.mp4`, `.mov`, `.mkv`).
- Voice under `assets/audio/` (`.mp3`, `.wav`).

**Binding rules:**
- `agent-bind` checks only the path, extension and that the file exists; real media validation happens in `assets`/`compose`.
- Binding the same file again changes nothing. Binding a different file sends a compiled manifest back to review, so the storyboard must be compiled again.
- Image scenes need a voice file. Video clips and sequences need one only if they have narration.
- `image_sequence` scenes take their `sequence` block from the brief and are validated with the same rules as the storyboard.

**Overwrite protection:**
- `agent-init` will not replace an existing manifest without `--replace`.
- `agent-compile` will not overwrite a populated storyboard without `--replace-storyboard`, and it keeps existing `audio_tracks` and subtitles.

### 5.4 Editing plan and render

```powershell
ai-video-editor agent-scene-apply ID       # editing_plan.json with plan_meta.source = "ai_assisted"
# or: ai-video-editor plan ID --style documentary --seed 12345
ai-video-editor approve-plan ID
ai-video-editor compose ID --deep
```

`agent-scene-apply`:
- needs the compiled storyboard's scene IDs and types to match the approved proposal;
- never changes media, narration or the storyboard;
- will not overwrite an existing `editing_plan.json` without `--replace`;
- always writes `approved_by_user=false`.

`compose` still enforces Core timing: narration plus padding that does not fit its video clip is rejected with a diagnostic.

### 5.5 Prompt export (no API cost)

```powershell
ai-video-editor agent-asset-prompts ID --format markdown --output .\asset_prompts.md
ai-video-editor agent-asset-prompts ID --format json --output .\asset_prompts.json
```

Prompts come from the script. When an approved, current scene plan exists, they are enriched with its media types and English prompts; narration always comes from the script.

### 5.6 Safety

- **When the network is used.** Only with `agent-script --request/--request-file`, `agent-scene-plan --model/--configured-provider` and the `asset generate-*` / `batch-*` commands. Imports and tests are offline.
- **Model names** passed to `--model` must be available to *your* account.
- **Generated content.** LLMs can hallucinate: review facts, sources and copyright before publishing.
- **Fixed tool list.** The agent tool registry (`agent-tools`) is a fixed allowlist and never runs shell commands or code supplied by a model.

---

## 6. Providers, API keys and asset generation

### 6.1 Provider catalog and routing

`config/providers.json` lists providers and models per task (`script`, `scene_plan`, `image`, `audio`). `video` must stay empty because no video adapter exists. It is shared by the GUI (**API Settings**) and the CLI (`provider …`).

| Task | OpenAI (built-in) | Google Gemini (built-in) |
|---|---|---|
| Script | `gpt-6-luna`, `gpt-4.1-mini` | `gemini-2.5-flash` |
| Scene plan | `gpt-6-luna`, `gpt-4.1-mini` | `gemini-2.5-flash` |
| Image | `gpt-image-2`, `gpt-image-1.5` (PNG) | `gemini-2.5-flash-image` (PNG/JPEG) |
| Voice | `gpt-4o-mini-tts`, `tts-1` (MP3) | `gemini-2.5-flash-preview-tts` (WAV, 24 kHz mono) |
| Video | prompt copy only | prompt copy only |

- **Built-in providers.** Their endpoints and adapters cannot be overridden, and they cannot be deleted.
- **Custom providers** use the `openai_compatible` adapter. They must implement the OpenAI **Responses API** (`POST /responses`) for script and scene plan; Chat Completions alone is not enough. For images they also need `images/generations` (base64 PNG), and for voice `audio/speech` (MP3).
- **Base URL rules.** The base URL must be HTTPS on port 443 with a hostname. Localhost, raw IP addresses, embedded credentials, query strings and fragments are rejected.
- **No verification.** Adding a model name does not check that the model exists, that your account can use it, or that it supports the task. Test one scene before a batch.
- **Routing.** The provider/model chosen for each task is stored in `.ai_video_editor_provider_settings.json`. An invalid or deleted choice falls back to the default.
- **Upgrading.** Back up a customised `config/providers.json` before upgrading. It contains configuration only, never secrets.
- **No video generation.** OpenAI's Videos API was shut down on 2026-09-24. Copy video prompts into an external tool and upload the resulting clip.

```powershell
ai-video-editor provider list
ai-video-editor provider models gemini
ai-video-editor provider add myapi --label "My API" --base-url https://api.example.org/v1
ai-video-editor provider add-model myapi --task audio --model my-tts
ai-video-editor provider set audio gemini --model gemini-2.5-flash-preview-tts
ai-video-editor provider set image openai --model gpt-image-2
```

### 6.2 API keys

- **Storage.** Keys live in the OS credential store via `keyring` (service `ai-video-editor-local`). Save them in **API Settings** or with `provider key-set ID`, which prompts without echo; use `--stdin` for automation. Never put a key on a command line.
- **Environment fallback.** `OPENAI_API_KEY` and `GEMINI_API_KEY` are used for the built-in providers. Custom providers have no environment fallback.
- **Never exposed.** The browser and the CLI can only check whether a key exists (`provider key-status ID`). Keys are never written to project files, `providers.json` or logs.
- **Linux without a keyring backend.** Saving fails rather than storing plaintext; use the environment variables instead.
- **Setting a key for one PowerShell session:**

  ```powershell
  $env:OPENAI_API_KEY = Read-Host "OpenAI API key" -AsSecureString | ForEach-Object { [System.Net.NetworkCredential]::new('', $_).Password }
  ```

### 6.3 Image and voice generation

```powershell
ai-video-editor asset usage my_project
ai-video-editor asset limits my_project --image 100 --audio 200
ai-video-editor asset generate-image my_project --scene 001 --auto-bind
ai-video-editor asset generate-voice my_project --scene 001 --voice Kore --auto-bind
ai-video-editor asset batch-image my_project --scenes 001,002,003 --yes --auto-bind
ai-video-editor asset batch-voice my_project --all-scenes --voice Kore --yes --auto-bind
ai-video-editor asset auto-bind my_project
```

**Consent before any paid call**
- The GUI shows a confirmation with provider, model, voice and scene count.
- The interactive CLI asks you to type `YES`.
- In scripts and CI, `--yes` is required; without it, non-interactive runs are rejected.

**Prompts**
- TTS always reads the scene **narration**, never an image or video prompt. Empty narration is rejected.
- Image generation uses the image prompt, or the video prompt for a `video_clip`. That result is only a reference image: it is never bound as the video clip.

**Local daily attempt budget**
- It is set per project, per UTC day; the default for a new project is 5 images and 10 voices.
- You choose any non-negative limit; `0` disables that kind.
- Every accepted request counts as one attempt, even if the provider later fails.
- The key, inputs, voice and budget are all checked *before* an attempt is counted.
- This is an accidental-spending guard: it is **not** your provider quota, credit or a cost estimate, and there are no automatic retries.

**Voices**
- Gemini: `Kore`, `Puck`, `Charon`, `Fenrir`, `Aoede`, `Zephyr`.
- OpenAI / compatible: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `onyx`, `nova`, `sage`, `shimmer`.
- The selection is saved per project and provider in `voice_preferences.json` and is used when no voice is given.
- These are the voices the app accepts; not every model supports every voice.
- Changing the voice never rewrites existing audio or existing bindings.

**Batches**
- Run sequentially and report success or failure per scene. A CLI batch exits non-zero if any scene failed.
- `--all-scenes` selects scenes with an image prompt (for images) or with narration (for voices).

**Generated files**
- Images: `assets/images/generated_image_<scene>_<hash>.png|jpg`.
- Voices: `assets/audio/generated_audio_<scene>_<hash>.mp3|wav`.
- Each is logged in `generated_assets.json` (provider, model, prompt hash, size; no prices). Existing files are never overwritten.

**Auto-bind**
- Binds only scene-tagged generated files of the matching kind and keeps existing bindings.
- While a script revision is pending, it accepts only files whose `prompt_sha256` matches the current narration or prompt.

---

## 7. Web GUI

```powershell
ai-video-editor ui                     # http://127.0.0.1:8765
ai-video-editor ui --port 8766
ai-video-editor ui --host localhost
```

Stop with `Ctrl+C`. After updating the source, restart the server and hard-refresh the browser (`Ctrl+Shift+R`). The bundled HTML pages need no Node.js. One shared sidebar, `static/sidebar.js` mounted in a Shadow DOM, links every screen.

| Route | Screen | Purpose |
|---|---|---|
| `/` | Dashboard | List and create projects; per-project workflow status (never spends tokens) |
| `/script-editor` | Script Editor | Generate, edit, import or export the script, Save, Approve, Export Brief |
| `/scene-planner` | Scene Planner | Generate/edit the scene plan, Save, Approve, Export Asset Brief |
| `/asset-workspace` | Asset Workspace | API/binding status, local limits, voice + batch actions, per-scene prompts and generation |
| `/render-studio` | Render Studio | Manifest, upload/bind media, prepare storyboard + editing plan, approve, render with progress, preview |
| `/api-settings` | API Settings | Provider keys, Add Provider / Add Model, provider/model per task |
| `/help` | Help | Built-in guide to the GUI and CLI workflows, files, costs and troubleshooting |

### 7.1 Screens

- **Script Editor**
  - Section 3 · *Review & Edit Script* has a general panel (title, language, style, target duration, summary, full narration) and, below it, the scene editor with a navigation list on the right. Switching scenes keeps unsaved edits.
  - **Save Changes** always revokes approval. **Approve Script** approves the *saved* script.
  - **Export Script JSON** / **Import Script JSON** round-trip an editable file (section 7.3). **Export Brief** produces the asset-pipeline brief.
- **Continue to Scene Planner** opens the Scene Planner with the same project selected. Section 4 · *Scene Decisions* has a scene list on the right for editing media type, prompt, duration, motion, intensity, transition and reasoning.
- **Asset Workspace**
  - Collapsible sections: 1 · Project, 2 · API & asset-binding status, 3 · API limits, *Batch API* (voice selector and **Tạo ảnh / voice hàng loạt**), 4 · per-scene prompts with checkboxes and **Check all**.
  - When the selection exceeds the remaining budget, the page states the minimum limit you need. It never raises a limit by itself.
- **Render Studio**
  - Section 2 · *Asset Binding* has a scene list on the right.
  - **Tạo / mở Manifest**, **Auto-bind assets**, upload any missing files.
  - **Tạo Storyboard & Editing Plan**: approves the manifest, compiles or recompiles the storyboard and creates an AI-assisted or rule-based plan. A replaced plan is backed up as `editing_plan_backup_<id>.json`.
  - **Approve Editing Plan** → **Render Video** → preview `output/final.mp4`.

If a scene plan expects a `video_clip`, a generated image will not be bound to it. Supply a real video, or revise the scene plan before creating the manifest. Media types are never changed silently.

### 7.2 Editing a script after rendering (script revisions)

You can revise a script at any stage. When the content actually changes:
- the derived files `agent_brief_generated.json`, `agent_scene_plan.json`, `agent_scene_brief.json`, `agent_manifest.json`, `editing_plan.json` and `timeline.json` move to `history/script_revision_<timestamp>/`, together with a copy of the previous script;
- `script_revision_pending.json` is created and script approval is reset;
- media files and `output/final.mp4` stay on disk, but the old video is not shown as current until a new GUI render succeeds;
- generating a replacement script with `replace` archives the same files, but only *after* the API call has succeeded.

You then redo the downstream steps: approve the script, recreate and approve the scene plan, export the brief, recreate the manifest, bind assets, then prepare and approve the editing plan. `history/` can use extra disk space; keep it until the new render is confirmed.

Voices generated from an earlier, wrong prompt are never deleted or re-bound automatically. Listen to the suspect MP3s, check `voice_file` in `agent_manifest.json`, and generate and bind replacements explicitly.

### 7.3 Editable script JSON

```json
{"format":"ai-video-editor-script","format_version":1,
 "content":{"title":"Example","language":"Tiếng Việt","style":"documentary","target_duration_seconds":10,
            "summary":"Example video","script":"Lời dẫn toàn video.",
            "scenes":[{"scene_id":"001","type":"image","narration":"Lời thoại của cảnh.",
                       "prompt":"Photo of the ocean","estimated_duration_seconds":10}]}}
```

- **Accepted inputs:**
  - this format;
  - a full `agent_script_draft.json`, whose approval and status fields are ignored;
  - a bare `content` object;
  - an edited **Export Brief** file. That updates only the title, scene type, narration and prompt, and rebuilds the full narration. Its scene IDs and order must stay the same; to add or remove scenes or change durations, use the script JSON format.
- **Validation.** Imports go through the same `validate_script` as manual edits. The web UI limits files to 2 MiB, and an invalid file changes nothing.
- **Replacing a script** needs confirmation (`replace=true` in the API). An identical import changes nothing; a changed import triggers the revision flow in section 7.2.
- **No API calls.** Import and export never contact a provider.

### 7.4 Background jobs and render progress

- **Job manager.** Generation and render jobs run in an in-memory job manager with 2 worker threads and at most one active job per project.
- **Lifetime.** Jobs survive closing the browser tab but not a server restart. Closing the browser does not cancel a provider request that was already sent.
- **Render runs as a subprocess.** It runs `compose --deep` through the CLI with a fixed argument list. Combined output goes to `logs/gui_render_last.log`. On failure or timeout the whole process tree, FFmpeg included, is terminated.
- **Render progress is stage-based** (read from `reports/render_checkpoint.json`):

  | Stage | Progress |
  |---|---|
  | Scenes | 5–80 % |
  | Segments | 80–90 % |
  | Video assembled | 92 % |
  | Done | 100 % |

  It is not frame-accurate and not an ETA; the percentage can hold still while a long scene encodes.
- **Timeout.** `timeout_minutes` (0–525600, default 0 = no limit) sets an outer wall-clock limit. Core FFmpeg steps keep their own timeouts.

---

## 8. CLI reference

```text
ai-video-editor [--projects-dir DIR] [--default-config FILE] COMMAND …
```

- `--projects-dir` defaults to `<repo>/projects`.
- `--default-config` defaults to `<repo>/config/default_project.json`.
- Validation errors print `ERROR: …` and exit 1.

| Group | Command | Description |
|---|---|---|
| Project | `init ID --title TITLE` | Create the project folder and JSON files |
| | `show ID` / `list` / `validate ID` | Metadata / list projects / structural validation |
| Core | `assets ID` | Probe media → `assets_report.json` (exit 1 on FAIL) |
| | `plan ID [--style S] [--seed N]` | Rule-based `editing_plan.json` |
| | `approve-plan ID` | Approve the editing plan (refreshes an outdated cache key) |
| | `timeline ID [--use-plan] [--transition none\|crossfade\|dissolve] [--length short\|medium\|long]` | Build `timeline.json` (`--transition`/`--length` are ignored with `--use-plan`) |
| | `render ID [--force] [--resume] [--supersample 1-4]` | Render `video_only.mp4` |
| | `audio ID` | Mix audio and mux `final.mp4` |
| | `qa ID [--deep]` | Read-only verification (exit 1 on FAIL) |
| | `compose ID [--deep] [--force] [--replan] [--style S] [--seed N]` | Whole pipeline |
| Script | `agent-script ID --request T \| --request-file F \| --import-json F [--model M] [--use-provider-settings] [--replace]` | Create the script draft |
| | `agent-script-status` / `agent-script-approve` / `agent-script-export ID [--replace]` | Inspect / approve / export brief |
| Scene plan | `agent-scene-plan ID --model M \| --configured-provider \| --import-json F [--replace]` | Create the scene proposal |
| | `agent-scene-status` / `agent-scene-approve` / `agent-scene-export [--replace]` / `agent-scene-apply [--replace]` | Inspect / approve / export brief / write the AI editing plan |
| Manifest | `agent-init ID --brief F [--replace]` | Create `agent_manifest.json` |
| | `agent-status` / `agent-bind ID --scene S --kind media\|voice --file P` / `agent-approve` / `agent-compile [--replace-storyboard]` | Bind, approve and compile the storyboard |
| | `agent-asset-prompts ID [--format markdown\|json] [--output F]` / `agent-tools` | Export prompts / list allowlisted tools |
| Providers | `provider list \| models P \| add P --label L --base-url U \| add-model P --task T --model M \| set TASK P --model M \| key-status P \| key-set P [--stdin]` | Catalog, routing and keys |
| Assets | `asset generate-image\|generate-voice ID --scene S [--voice V] [--yes] [--auto-bind]` | Single paid generation |
| | `asset batch-image\|batch-voice ID --scenes A,B \| --all-scenes [--voice V] [--yes] [--auto-bind]` | Batch generation |
| | `asset auto-bind ID` / `asset usage ID` / `asset limits ID --image N --audio N` | Binding and local limits |
| GUI | `ui [--host 127.0.0.1\|localhost] [--port 8765]` | Start the web GUI (needs `[ui]`) |

---

## 9. HTTP API reference

Interactive docs: `/api/docs`. All routes are under `/api`. Every paid route needs `"confirmed": true`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET, PUT | `/provider-settings` | Catalog, key presence and task routing / save routing |
| PUT, DELETE | `/provider-settings/keys/{provider}` | Store / delete a key |
| POST | `/provider-settings/providers` | Add a provider (`provider_id`, `label`, `base_url`) |
| DELETE | `/provider-settings/providers/{provider}` | Delete a custom provider |
| POST | `/provider-settings/models` | Add a model (`provider_id`, `task`, `model`) |
| DELETE | `/provider-settings/models/{task}/{provider}/{model}` | Delete a model |
| GET, POST | `/projects` | List / create (`project_id`, `title`) |
| GET | `/projects/{id}` | Metadata and workflow status |
| GET, PUT | `/projects/{id}/script` | Read / save the edited script (revokes approval) |
| POST | `/projects/{id}/script/generate` | Generate via the configured provider (`requirement`, `replace`) |
| GET | `/projects/{id}/script/editable-export` | Editable script JSON |
| POST | `/projects/{id}/script/import` | Import (`content`, `replace`) |
| POST | `/projects/{id}/script/approve`, `/script/export` | Approve / export brief (`replace`) |
| GET, PUT | `/projects/{id}/scene-plan` | Read / save the edited plan |
| POST | `/projects/{id}/scene-plan/generate`, `/approve`, `/export` | Generate / approve / export the asset brief |
| GET | `/projects/{id}/asset-prompts`, `/asset-prompts.md` | Prompt pack as JSON / Markdown |
| GET, PUT | `/projects/{id}/assets/voice-settings` | Voices for the audio provider / save the selection (`voice`) |
| POST | `/projects/{id}/assets/generate` | Legacy synchronous single generation |
| POST | `/projects/{id}/assets/generate-job`, `/assets/batch-job` | Background single / batch generation |
| GET | `/projects/{id}/assets/status` | Candidates, manifest, missing assets, usage, active job |
| PUT | `/projects/{id}/assets/limits` | Set daily limits (`image`, `audio`) |
| POST | `/projects/{id}/assets/manifest`, `/auto-bind`, `/bind`, `/upload` | Manifest and binding (upload ≤ 200 MB) |
| GET | `/projects/{id}/jobs/{job_id}` | Job status (`percent` for renders) |
| POST | `/projects/{id}/render/prepare` | `approve_manifest`, `replace_stale_plan` |
| POST | `/projects/{id}/render/approve-plan` | Approve the editing plan |
| POST | `/projects/{id}/render/start` | `confirmed`, `timeout_minutes` |
| GET | `/projects/{id}/render/output` | Stream `final.mp4` (409 while a script revision is pending) |

---

## 10. Security model

- **Network exposure.** The server binds to loopback only, and `--host` accepts only `127.0.0.1` or `localhost`.
- **Host check.** `TrustedHostMiddleware` allows only `127.0.0.1`, `localhost` and `[::1]` (`testserver` is added only with `create_app(testing=True)`), which guards against DNS rebinding.
- **Cross-site requests.** Mutating requests from another origin, or with a malformed `Origin`, get HTTP 403.
- **No authentication or CSRF token.** The GUI is for a single trusted local user. Never expose it on a network or behind a reverse proxy.
- **Files and commands.** The API never serves arbitrary project files, accepts only project-relative bind paths, and never runs shell commands. Uploads are written under generated names and validated by extension (plus a header check for images).
- **Secrets.**
  - `.gitignore` excludes `.env*`, `*.pem`, `*.key`, `secrets.json`, logs and the provider-settings file.
  - Ignoring a file does not untrack one already committed: check `git ls-files` and revoke any exposed key.
  - A custom provider receives the key you saved for it and your prompts, so only add providers you trust.

---

## 11. Testing

```powershell
py -m pip install -e ".[ui,test]"
py -m unittest discover -s tests -v
```

| Group | Pattern |
|---|---|
| Core (FFmpeg integration tests skip without FFmpeg) | `test_assets.py`, `test_timeline.py`, `test_renderer.py`, `test_audio.py`, `test_qa.py`, `test_regressions_110*.py`, … |
| Agent | `test_agent*.py` |
| GUI (skip without the `[ui,test]` extras) | `test_gui_phase24*.py`, `test_hotfix_phase2410.py`, `test_provider_catalog_phase249.py`, `test_user_limits_phase2411.py`, `test_phase2412.py`, `test_voice_selection_phase2413.py`, `test_asset_batch_layout_phase2414.py`, `test_help_layout_phase252.py`, `test_script_json_import_phase2512.py`, `test_code_review_phase247.py` |
| CLI parity | `test_cli_phase251.py` |

Provider calls and render jobs are mocked, so no tokens are spent. Real provider access and billing, Windows 11 + FFmpeg 9 renders and browser behaviour are manual acceptance checks. If many GUI tests report *skipped*, the test HTTP client is missing; install the `[ui,test]` extras.

---

## 12. Troubleshooting

| Message / symptom | Fix |
|---|---|
| `Editing plan requires approval` | `ai-video-editor approve-plan ID` (or **Approve Editing Plan**) |
| `Stale timeline (…)` | `ai-video-editor timeline ID --use-plan`, then render/audio again, or just `compose` |
| `Stale render checkpoint` / `Render checkpoint missing` | `ai-video-editor render ID` before running `audio`/`qa` separately |
| `decoded … samples, timeline expects …` | Rerun `assets` and `timeline`: the voice file changed |
| `narration plus padding exceeds video clip length` | Use a longer clip or shorter narration |
| `Another render may be running` | Wait, or delete `work/render.lock` if no render process exists |
| Burn-in fails | Install an FFmpeg build with libass (`ffmpeg -filters` must list `subtitles`) |
| `Local … daily cap reached` | Raise the project's limit (Asset Workspace → API limits, or `asset limits`) |
| `Configure API key for …` | Save the key in API Settings or `provider key-set`; no attempt was counted |
| `Storyboard must be recompiled…` / `Editing Plan must be regenerated…` | Assets changed after compile: click **Tạo Storyboard & Editing Plan** and approve the new plan |
| Old video hidden / `/render/output` returns 409 | A script revision is pending; finish the downstream steps and render again |
| `Video missing: run … render` | Run `render` (or `compose`) before `audio` |
| Too quiet or loud voice | Levels follow the supplied files; loudness mastering is not implemented |

Verify a result manually:

```powershell
ffprobe -v error -count_frames -show_entries stream=codec_type,codec_name,profile,pix_fmt,width,height,nb_read_frames,sample_rate,channels,color_space -of json "projects/ID/output/final.mp4"
```

---

## 13. Known limitations

- **Platform.** Windows 11 + FFmpeg 9 acceptance is manual; automated FFmpeg tests were developed on Linux.
- **Colour.** HDR or unusually tagged input is converted as standard SDR, without ICC profile management.
- **Narration modes.** `voice_mode` supports `per_scene` only; single-track narration with alignment is not implemented.
- **Audio.** No ducking, looping, LUFS normalisation or true-peak mastering.
- **QA blind spots.**
  - QA cannot prove that the voice or picture content matches the script.
  - QA cannot detect a stale final mix after changing only background tracks or subtitle cues. Rerun `audio` or `compose` after such changes.
- **Concurrency.** Writing to one project concurrently from several processes (for example GUI and CLI at once) is not supported.
- **Jobs and progress.** GUI jobs are not persistent across server restarts. Render progress is stage-based.
- **Video generation.** There is no video generation API; video prompts are copy-only.
- **Version metadata.** `pyproject.toml` still reports version `2.4.7` with a "Phase 2.4.5" description.

---

## 14. Upgrading an existing installation

1. Stop `ai-video-editor ui` and back up the repository and `projects/`.
2. Apply the new source (Git pull, or extract a release ZIP over the repository). **Never overwrite or delete `projects/`.** Keep your customised `config/providers.json`, provider settings and keyring entries.
3. Reinstall and test:

   ```powershell
   py -m pip install -e ".[ui,test]"
   py -m unittest discover -s tests -v
   ```

4. Restart `ai-video-editor ui` and hard-refresh the browser.
5. After a cache-identity change (for example Core 1.10.2 / 1.10.3), the first `compose` may re-render. If an older plan is reported stale, run `approve-plan` once; this keeps curated choices. Use `--replan` only if you want a new plan.
6. Remove any legacy `src/ai_video_editor/agent.py` file: it must not sit next to the `agent/` package.

---

## 15. Technical specifications per phase

Condensed behavioural contracts. The newest behaviour is described in sections 4–7; this section records each phase's guarantees and deliberate limits.

**1.1 — Project management.**
- `ProjectManager` provides `create_project`, `load_project`, `save_project`, `get_project_status` and `list_projects`.
- Projects are created in a staging folder and renamed into place; a duplicate ID is an error.
- Every JSON file is written atomically on its own; there is no cross-file transaction.
- `schema_version` must be `1.1`, and IDs are restricted to ASCII. The only job state is `CREATED`.

**1.2 — Asset Manager.**
- FFprobe reads metadata and FFmpeg counts the real decoded/resampled PCM samples.
- Only common uncompressed PCM WAV variants are accepted.
- A file that resolves outside the project is rejected.
- Only media referenced by the storyboard is validated.
- Disk estimate: `max(256 MiB, 3 × media bytes)`.
- MP3 encoder delay is kept as decoded.

**1.3 — Timeline engine.**
- The invariants in section 4.5. A failed validation leaves the previous `timeline.json` unchanged.
- `validate_timeline` checks only geometry; `build_timeline` re-runs the asset check first.

**1.4 — Rule-based planner.**
- Deterministic and offline (seeded PRNG); the style table is in 4.4.
- The plan never writes to the storyboard; `merge_plan_into_storyboard` returns a copy.
- `config.planning.use_ai` is ignored.

**1.5 — Video render engine.**
- Per-scene FFV1 clips; each transition is rendered separately and lasts exactly its overlap.
- The output is verified with FFprobe and published through `.partial.mp4`.
- One renderer per project.

**1.6 — Voice engine.**
- Voices are decoded to PCM16 and must match the timeline's sample count exactly; there is no stretching or trimming.
- The video is stream-copied; the result is AAC-LC 192 kbps with a ±2048-sample tolerance.
- The output is published through `final.partial.mp4`.

**1.7 — Checkpoint and recovery.**
- The render identity covers the plan, timeline, input hashes, supersampling, size, FPS, fit, encoder preset and engine versions.
- Cache rules and failure behaviour are described in 4.6.

**1.8 — Integration QA.**
- Checks are reported as PASS, FAIL or BLOCKED and continue independently.
- Deep mode uses `blackdetect` (≥ 1 s) and `silencedetect` (−50 dB, ≥ 0.65 s overlapping a voice) and only produces warnings.

**1.9 — Image sequences.**
- One source image becomes one output frame, at the project FPS.
- At most 18,000 frames. Each frame's hash is part of the cache key.
- No interpolation, stabilisation or variable frame durations.

**1.10 — Mixed media.**
- Adds `video_clip` scenes, global `audio_tracks`, subtitle modes and `compose`.
- Videos are normalised to the project FPS and size.

**1.10.1 — Source-video audio.**
- Embedded clip audio is aligned to the scene start and faded over transitions; details and migration in 4.2.

**1.10.2 — Correctness hotfix.**
- Video stream duration; correct BT.709 conversion; FFmpeg preflight.
- Checkpoint SHA-256 checks before `audio` and `qa`; `compose --replan/--style/--seed`.
- Burn-in moved into the single H.264 encode; audio-only changes skip the video re-encode; peak limiter; SRT validation.

**1.10.3 — Precision.**
- `alimiter latency=1` keeps samples aligned.
- New still-image motion cache version (`image-motion-1.10.3`).
- Narrower plan cache key (older plans may need one `approve-plan`).
- Subtitle `fontsdir` follows `font_file`; FFmpeg ≥ 6.1; project-relative checkpoints.

**2.1 — Agent foundation.**
- Offline, human-in-the-loop manifest flow with a fixed tool allowlist and no remote calls.
- The approval hash covers the creative content (title, IDs, types, narration, prompts, sequences); bindings may change after approval.

**2.2 — Script generator.**
- The draft (`schema_version 2.2`) holds the requirement, content, provider-reported usage and approval hash; keys are never stored.
- `--import-json` is fully offline. An existing draft is checked before any billable call.

**2.3 — Scene planner.**
- The proposal must match the approved script; editing the script makes it stale.
- `agent-scene-apply` writes `plan_meta.source = "ai_assisted"`, prompt version `ai-scene-v2.3.0`, `approved_by_user = false`.
- Any well-formed provider ID is accepted in the plan metadata.

**2.4.x — GUI.** Everything is described in sections 6–7. Each paid request is confirmed explicitly, downstream approvals are never reused silently, and existing files are never overwritten without confirmation.

**2.5.1 — CLI parity.** The provider, asset, voice and limit commands work without FastAPI and share their logic and files with the GUI.

---

## 16. Changelog

Newest first.

**After 2.5.1 — Help and layout**
- Built-in `/help` page linked from the shared sidebar.
- Script Editor separates the general fields from scene editing.
- Asset Workspace moves the voice selector and batch buttons into a full-width *Batch API* section between the API limits and the scene list.

**2.5.1.2 — Editable script JSON import/export**
- Export and import an editable script file (section 7.3); importing an edited Export Brief is also supported.

**2.5.1 — CLI feature parity**
- `provider …` and `asset …` commands.
- `agent-script --use-provider-settings` and `agent-scene-plan --configured-provider`.

**2.4.14 — Batch layout**
- The batch image/voice buttons sit above **Check all** in the scene panel; this was later moved again by the help/layout update.

**2.4.13 — Voice selection**
- Per-project voice per provider (`voice_preferences.json`) and the `/assets/voice-settings` routes.
- The voice is validated before any attempt is reserved.

**2.4.12 — Script revisions and render progress**
- Scripts can be edited after rendering, with archiving to `history/` (section 7.2).
- Prompt-hash–checked auto-bind while a revision is pending.
- Render `timeout_minutes`; stage-based render `percent`; GUI render log.
- Scene lists in the Scene Planner and Render Studio.

**2.4.11 — User-controlled limits**
- No fixed 20-image / 50-voice maximum; any non-negative limit.
- No 50-scene batch cap; the GUI explains the minimum limit needed.

**2.4.10 — Critical hotfix**
- Single, job and batch routes share one `_asset_prompt`, so TTS always reads the narration.
- AI plans accept Gemini and custom provider IDs.
- `.m4a` uploads are rejected before anything is written.
- Test guards also catch `RuntimeError`.
- Binding the same file again changes nothing, and **Prepare** recompiles with a plan backup.
- Malformed provider responses give HTTP 422.
- The key is checked before an attempt is counted; a damaged limits file gives 422.
- Production hosts exclude `testserver`.

**2.4.9 — Editable provider catalog**
- `config/providers.json` with Add/Delete Provider and Model; custom `openai_compatible` providers.

**2.4.8 — Shared sidebar**
- One Shadow-DOM sidebar for all screens; collapsible Asset Workspace sections.

**2.4.7 — Code-review remediation**
- Secret-safe `.gitignore`.
- Friendly `ui` dependency error; `--host` honoured.
- `[test]` extra; manifest extensions aligned with the Core; shared `validate_sequence`.
- Clean errors for invalid OpenAI JSON; correct timeline plan source; `TrustedHostMiddleware`.

**2.4.6 — Multi-provider settings**
- OpenAI and Gemini routing per task, keyring storage, Gemini adapters, batch generation, scene navigation.

**2.4.5 — Asset jobs and Render Studio**
- Background jobs, local daily budgets, auto-bind, upload/bind, prepare/approve/render/preview.

**2.4.4 — Asset prompts**
- Prompt view and export, `agent-asset-prompts`, per-asset OpenAI image and speech generation.

**2.4.3 — Scene Planner GUI.**

**2.4.2 — Script Editor GUI.**

**2.4.1 — Local dashboard**
- FastAPI on loopback, zero-build dashboard (static HTML; the early React/Vite prototype was removed in 2.5.1.7).

**2.3 — AI scene planner.**

**2.2 — Script generator.**

**2.1 — Agent foundation**
- Replaces the old single-file `agent.py`.

**Core 1.10.3 / 1.10.2 / 1.10.1 / 1.10 / 1.9 / 1.8 / 1.7 / 1.6 / 1.1–1.5** — see section 15.
