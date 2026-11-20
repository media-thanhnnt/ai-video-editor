# Phase 2.1 — Agent Architecture & Manual Assets (MVP)

## Scope

This release adds an **offline, human-in-the-loop orchestrator foundation** atop accepted Core 1.10.3. It intentionally does **not** call OpenAI/Gemini, generate script with an LLM, download assets, call TTS, or schedule rendering automatically. A future LLM adapter can produce a structured brief consumed by this layer.

## Lifecycle

1. `agent-init` — import user/LLM-authored structured brief as `agent_manifest.json`, without modifying the existing storyboard.
2. `agent-status` — report missing media/voice assets.
3. `agent-bind` — associate an existing project-relative, type-checked media/voice file with a scene.
4. `agent-approve` — approve the content plan when assets are present, saving a SHA-256 hash of its creative intent.
5. `agent-compile` — compile approved manifest to a valid Phase 1 `storyboard.json`. Refuse to replace populated storyboard without `--replace-storyboard`.
6. Use the accepted Core's `plan`, `approve-plan` where required, and `compose --deep`.

## Security & assumptions

- Tools are a fixed allowlist; no arbitrary shell, dynamic code execution or remote API calls.
- Paths must remain under the project and expected media directories; symlink escapes are rejected.
- The media files must be supplied manually. `agent-bind` only checks path/extension/existence; Core `assets` validates real media after compile.
- `image` scenes require narration and a local voice file under existing Core schema. `video_clip` scenes may be silent. Sequences require a valid `sequence` specification in the brief, and Core performs detailed frame validation.
- Changes to approved creative content invalidate the approval hash.
- `agent-compile` deliberately does not start rendering, spend tokens, or overwrite a populated storyboard by default.
- If a project already has a valid `editing_plan.json`, the existing Core's plan freshness/approval logic still applies after storyboard compilation.

## Next phases

Phase 2.2: optional LLM provider adapter for theme -> script; Phase 2.3: script -> scene plan; Phase 2.4: assisted manual asset UX; Phase 2.5: orchestration into Core; Phase 2.6: QA-based revisions.
