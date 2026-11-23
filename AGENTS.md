# AGENTS.md — fast orientation for AI coding agents

Read this file first. It is the shortest correct path to understanding and safely changing
this repository. The user-facing manual is `README.md` (long); per-phase change notes live in
`docs/changelog/`. Current state: **Phase 2.5.1.7**, about 6.6k lines of Python, 294 unittest tests.

## 1. What this is (one paragraph)

AI Video Editor is a **local, deterministic video editor** (Python 3.10+, FFmpeg/FFprobe ≥ 6.1,
OpenCV, Pillow, numpy). It turns a *storyboard* of still images, image sequences or video clips,
plus one narration audio per scene, into an H.264/AAC MP4. On top of that rendering **Core** sits an
optional, human-approved **Agent** layer: Script → Scene Plan → generated images/voices via
OpenAI/Gemini → manifest → storyboard. Both a **CLI** (`ai-video-editor …`) and a **localhost web
GUI** (FastAPI + static HTML, `ai-video-editor ui` → http://127.0.0.1:8765) drive the same code
and the same per-project folders. Rendering never touches the network. Paid API calls happen only
after explicit user consent.

## 2. Map of the source (`src/ai_video_editor/`)

```text
Core (offline, deterministic)                 Agent (AI, approval-gated)            API / GUI
project.py   config+storyboard schema, I/O    agent/script_generator.py  Script     api/app.py          all HTTP routes (FastAPI)
assets.py    FFprobe media checks, hashes     agent/scene_planner.py     Scene Plan api/workflow.py     GUI glue: manifest, prepare, render subprocess
planner.py   rule-based editing plan          agent/manifest.py          bindings   api/provider_settings.py  provider catalog + OS-keyring keys
timeline.py  frame/sample-exact timeline      agent/asset_workflow.py    prompts,   api/gemini_provider.py    Gemini REST (text/image/TTS)
renderer.py  scene clips, motion, overlay,                               OpenAI img/TTS api/jobs.py      in-memory background jobs
             transitions, final H.264         agent/tool_registry.py     allowlist  api/limits.py       per-project daily API attempt caps
audio.py     voice mix, music, ducking,       overlay.py   text-card PNG layers     api/voice_settings.py     TTS voice lists/preferences
             loudness, subtitles, mux                                               api/static/*.html   GUI pages (plain HTML/JS, no build)
qa.py        read-only output verification    cli.py       all CLI commands
sequence.py  FFmpeg fit filters               cli_assets.py  CLI paid generation (shares GUI logic)
ffmpeg_support.py  FFmpeg version/capability preflight
```

Repo root: `config/default_project.json` (copied into every new project), `config/providers.json`
(provider/model catalog, no secrets), `examples/` (sample storyboards/scripts),
`scripts/create_demo_assets.py`, `tests/`, `projects/` (user data, gitignored; never commit or
delete it).

## 3. End-to-end data flow

```text
requirement ─▶ agent_script_draft.json ─approve─▶ agent_brief_generated.json
                       └─▶ agent_scene_plan.json ─approve─▶ agent_scene_brief.json
brief ─▶ agent_manifest.json (bind media/voice/overlay files) ─approve─▶ compile ─▶ storyboard.json
storyboard ─▶ editing_plan.json (rule-based or ai_assisted) ─approve-plan─▶
assets ─▶ timeline.json ─▶ render (work/scenes/*.mkv FFV1 → output/video_only.mp4)
       ─▶ audio (work/audio/voice_mix.wav [+ final_mix.wav] → output/final.mp4) ─▶ qa (reports/*.json)
```

`compose PROJECT --deep` runs assets → plan check → timeline → render → audio → QA and reuses caches.
The GUI "Render Video" button runs that same CLI command as a subprocess (`api/workflow.render_cli`).

## 4. Invariants you must not break

1. **Approval is hash-bound.** Script, Scene Plan, manifest and editing plan approvals store a
   SHA-256 of the creative content. Any content change must reset approval. Never auto-approve.
2. **Timing is integer frames and samples.** Voice is never trimmed or stretched. A scene lasts
   `pad_head + voice + pad_tail` (≥ `min_scene_seconds`). Transitions overlap neighbours.
   QA tolerance is ±2048 AAC samples.
3. **Cache identity.** Scene clips are keyed by media hash, motion, frames, resolution, fps, fit and
   overlay hash. `compute_render_identity` keys the checkpoint. **New settings join a key only when
   they differ from the historical default** (see `_motion_key`, `_crf`), so upgrading never forces
   a re-render. Follow that pattern when you add a setting that changes pixels.
4. **Everything stays inside the project.** Use `check_relpath`/`ensure_inside`. Media lives in
   fixed folders: `assets/images|audio|videos|sequences|overlays|subtitles|fonts`.
   Write JSON with `atomic_write_json`.
5. **Paid calls need consent and budget.** GUI routes require `"confirmed": true`; the CLI requires a
   typed `YES` or `--yes`. `api/limits.reserve()` is called *before* every provider call, after key
   and input validation. TTS reads the **narration**, never an image prompt.
6. **Secrets.** Keys live only in the OS keyring (service `ai-video-editor-local`) or in
   `OPENAI_API_KEY`/`GEMINI_API_KEY`. Never write them to files, logs or responses.
7. **Local-only server.** Loopback host, `TrustedHostMiddleware`, cross-origin mutations rejected.
   Do not add routes that serve arbitrary project files or run shell commands.
8. **Config keys are optional with defaults** (`project._validate_optional_settings`). Old project
   `config.json` files must keep working. Do not add a key that no code reads (no "dead config").

## 5. Conventions and gotchas

- Errors: domain code raises `ValidationError` (or subclasses `RenderError`, `AudioError`,
  `PlanError`, `AssetError`). `cli.main` prints `ERROR: …` and returns 1. `app.py` maps errors to
  HTTP 4xx (`_safe_script_error`: 409 for "exists/approved/changed", otherwise 422).
- Code style is dense: long lines, few blank lines, some one-line functions. Match the style of the
  file you edit, and avoid reformatting unrelated code.
- **Windows code page:** the user's machine uses **cp932**. CLI output goes through
  `cli._utf8_stdio()`, and the GUI render subprocess sets `PYTHONIOENCODING=utf-8`. Any new
  subprocess that prints Vietnamese or Japanese text needs the same treatment.
- **Prompt convention:** a Script scene `prompt` may contain `… TEXT OVERLAY: tok1; tok2; …`.
  `overlay.split_prompt` removes that part before any image API call (`make_prompt_pack`), and
  `overlay-generate` renders it as a transparent card PNG. Labels it understands: `Romaji:`,
  `Từ gốc:`, `Nghĩa:`, `Ghi chú:`, `English:`, `Tiếng Việt:`. Tokens like `Thẻ N: word → answer`
  make a quiz card.
- Image aspect ratio follows `config.output`: Gemini sends `imageConfig.aspectRatio`, OpenAI picks
  1536x1024, 1024x1536 or 1024x1024. `image.fit_mode=auto` uses cover when the ratio is within 3%,
  otherwise blur_fill.
- Provider adapters: `openai`, `openai_compatible` (HTTPS only; Responses API for text) and
  `gemini`. All asset calls in `app.py` go through `_call_asset_provider`.
- GUI pages are self-contained HTML files with inline JS. They share `sidebar.js`/`sidebar.css`.
  Each new page must be listed in `pyproject.toml` package-data **and** get its own route in `app.py`.

## 6. Where to change things

| Task | Files |
|---|---|
| New storyboard scene field | `project.validate_storyboard` → `assets.match_scene_assets`/`validate_assets` → `timeline._timeline_asset_keys` → `renderer` (scene key + identity) → `agent/manifest` (bind/compile) |
| New config setting | `config/default_project.json` + `project._validate_optional_settings` (with a default) + the consuming module; keep cache keys stable for the default |
| New provider or model behaviour | `api/provider_settings.py`, `agent/asset_workflow.py` (OpenAI), `api/gemini_provider.py`, `app._call_asset_provider`, `cli_assets.generate` |
| New HTTP route | `api/app.py` (pydantic request model at top; check `jobs.active` for mutations) |
| New CLI command | `cli.py`: parser in `main()` plus an `elif args.action == …` branch |
| Audio mix | `audio.mux_project_audio`: pass 1 mixes layers to float WAV; pass 2 applies loudness gain + limiter and muxes |

## 7. Run and test

```powershell
py -m pip install -e ".[ui,test]"
# Tests: ALWAYS disable real credentials (the dev machine has real API keys in the keyring)
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
Remove-Item Env:OPENAI_API_KEY, Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
py -m unittest discover -s tests          # 294 tests, ~40 s; FFmpeg tests skip if FFmpeg is missing
ai-video-editor ui                         # GUI; restart it after code changes, then Ctrl+Shift+R
ai-video-editor compose PROJECT --deep     # full local render of an existing project
```

Test files are named after the phase that introduced them (`test_gui_phase24x.py`,
`test_review_fixes_phase2516.py`, `test_features_phase2517.py`, …). Provider calls must be mocked:
patch `ai_video_editor.cli_assets.get_key` / `generate_openai_asset` / `gemini_asset` or
`api.gemini_provider.request_model`. Never let a test reach the network.

## 8. Per-project folder (`projects/<id>/`)

`config.json`, `storyboard.json`, `job_state.json` · `agent_script_draft.json`,
`agent_brief_generated.json`, `agent_scene_plan.json`, `agent_scene_brief.json`,
`agent_manifest.json` · `editing_plan.json`, `timeline.json` · `generated_assets.json`,
`api_request_limits.json`, `api_request_usage.json`, `voice_preferences.json` ·
`script_revision_pending.json` + `history/` (archived artifacts after Script/Scene Plan edits) ·
`assets/…`, `work/` (cache, checkpoints, `render.lock`), `output/` (`final.mp4`, `video_only.mp4`,
`subtitles.srt`), `reports/`, `logs/gui_render_last.log`.

## 9. Known limitations (do not "fix" without being asked)

- `voice_mode` supports `per_scene` only. There is no speech recognition or word-level alignment.
- There is no video-generation API; video prompts are copy-only.
- GUI jobs are in-memory: a server restart forgets them. Render progress is stage-based.
- Overlays apply to `image` scenes only. Imported SRT files are not re-wrapped.
- Git history before 2.4 is coarse; read `docs/changelog/` for why things are the way they are.
