# AI Video Editor — Phase 2.5.1.3: Source Review Hotfix

This release is based on the uploaded **src_new_version.zip**. It keeps existing CLI and web routes, project storage, and provider catalog behavior, except for the documented safety fixes below.

## Review fixes

1. **API key isolation:** Script/Scene API routes now require a key for the selected provider before any billable API call. OpenAI-compatible helper functions never fall back to `OPENAI_API_KEY` for a third-party `base_url`. Only the exact official `https://api.openai.com/v1` endpoint supports the traditional environment-variable fallback. The GUI is designed to obtain keys from the OS keyring.
2. **Editing Plan integrity:** The render Prepare step generates and validates a new plan **in memory** before replacing the existing plan. Replacement uses `atomic_write_json` and backs up an old file. If plan generation fails, the previous `editing_plan.json` remains unchanged.
3. **Render Prepare UX:** First attempt does not force `replace_stale_plan`. When the plan truly needs replacement, a second explicit confirmation is requested. Recompiling a storyboard after a voice change does not revoke an otherwise still-valid plan approval.
4. **Packaging:** `static/help.html` is included in `pyproject.toml` package data and is verified in a normal non-editable wheel.
5. **Asset prompt hashing:** Automatically bound generated assets are matched using trimmed prompts from the active Script/Scene prompt pack. This matches hash generation and prevents stale prompt reuse after editing.
6. **CLI compose:** Successful Compose + QA clears `script_revision_pending.json`, just like a successful render from the GUI.
7. **Provider deletion:** Removes credentials from OS keyring *before* deleting the provider record. If keyring deletion fails, provider removal is refused, preventing reuse of the ID with an old key.
8. **Mutable default catalog:** Catalog model lists are copied before adding/removing models so default global lists do not mutate without a saved `providers.json`.
9. **Invalid registry:** GET `/api/provider-settings` returns a controlled HTTP 422 for invalid provider JSON/configuration, instead of HTTP 500.
10. **No narration in batch:** POST `/assets/batch-job` responds with HTTP 422 and never submits an API job when the chosen scene does not have narration for an audio task.
11. **Imported brief validation:** A non-string `narration` is rejected with HTTP 422 before script concatenation.
12. **CLI errors:** `agent-asset-prompts --output`, negative `asset limits`, and `asset usage` with invalid limits are handled by `ValidationError` with concise `ERROR:` messages and exit code 1.
13. **Broken help reference:** The GUI missing-assets error no longer references a deleted `README_PHASE2_4_1.md` file.

The current source already guards `FastAPI`/`httpx` test imports against `RuntimeError` as well as `ImportError` and already reads the manifest only once without referencing `agent_plan.json`. These are retained and covered by the existing regression tests.

## Update on Windows

1. Back up your repository and the `projects/` folder.
2. Stop the running GUI server (`Ctrl+C`).
3. Extract **Changed-Files.zip** over your current source. It does not include `projects/` or user-specific settings.
4. Run:

```powershell
py -m pip install -e ".[ui,test]"
py -m unittest discover -s tests -p "test_review_src_new_hotfix.py" -v
py -m unittest discover -s tests -p "test_gui_phase24*.py" -v
py -m unittest discover -s tests -p "test_agent*.py" -v
ai-video-editor ui
```

Refresh browser with `Ctrl+Shift+R`.

For Linux/headless usage, the original CLI remains available. The `keyring` package must be installed and configured when managing provider secrets. Provider deletion fails safely if the credential store cannot be accessed.

## Security and runtime notes

- **Do not put API keys inside `providers.json`** or command-line arguments. Use API Settings / OS keyring, or `OPENAI_API_KEY` only for the official OpenAI endpoint.
- If a provider key was already sent to an unintended endpoint in previous versions, revoke/rotate that key. A code update cannot undo prior exposure.
- The GUI does not automatically revert storyboard compilation if rendering preparation encounters an error, but it **preserves the previous Editing Plan file**. Review project status before re-running Prepare.
- Automated provider requests use mocks and are not a substitute for a paid real-API smoke test.
- Rendering FFmpeg on your Windows system has not been fully revalidated in this environment.

## Files affected

`src/ai_video_editor/api/app.py`, `src/ai_video_editor/api/workflow.py`, `src/ai_video_editor/api/provider_settings.py`, `src/ai_video_editor/api/static/render-studio.html`, `src/ai_video_editor/agent/script_generator.py`, `src/ai_video_editor/agent/scene_planner.py`, `src/ai_video_editor/agent/asset_workflow.py`, `src/ai_video_editor/cli.py`, `pyproject.toml`, updated GUI/provider regression tests, and `tests/test_review_src_new_hotfix.py`.
