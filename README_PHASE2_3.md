# Phase 2.3 — AI Scene & Storyboard Planner

Phase 2.3 builds on the accepted Phase 2.2 script generator and Phase 2.1 manual asset workflow. Core Phase 1.10.3 is retained. It proposes **media type, English prompt, estimated duration, motion, intensity and transition**. The user explicitly approves proposals; no image/video/TTS API is invoked.

## Setup (Windows PowerShell)

From the repository root, back up code, replace source/tests/examples from the 2.3 package, preserve `projects/`, then:

```powershell
py -m pip install -e .
py -m unittest discover -s tests -p "test_agent*.py" -v
```

## Quick start with existing approved Phase 2.2 script

Your `openai_api_test_001` project already has `agent_script_draft.json` with `SCRIPT_APPROVED` (3 scenes). The following is a **real OpenAI API call**, billed separately:

```powershell
ai-video-editor agent-scene-plan openai_api_test_001 --model gpt-6-luna
ai-video-editor agent-scene-status openai_api_test_001
```

Inspect `projects/openai_api_test_001/agent_scene_plan.json` before approving:

```powershell
ai-video-editor agent-scene-approve openai_api_test_001
ai-video-editor agent-scene-export openai_api_test_001
```

`agent-scene-export` outputs `projects/openai_api_test_001/agent_scene_brief.json`, ready for the existing Phase 2.1 workflow:

```powershell
ai-video-editor agent-init openai_api_test_001 --brief .\projects\openai_api_test_001\agent_scene_brief.json
# Then user supplies media and voice via agent-bind, agent-approve, agent-compile.
```

**Do not run `agent-init` without `--replace` when a manifest already exists**. Never overwrite prepared project assets accidentally. For a clean test, create a new project and import/approve the Phase 2.2 script before planning.

After assets have been bound and compiled, you may apply the AI-approved editing decisions to the matching Core storyboard:

```powershell
ai-video-editor agent-scene-apply openai_api_test_001
ai-video-editor approve-plan openai_api_test_001
ai-video-editor compose openai_api_test_001 --deep
```

`agent-scene-apply` **does not** change storyboard scene IDs, media references, narration, or source files. It rejects a storyboard with scene types/order inconsistent with the AI proposal, and refuses to overwrite an existing `editing_plan.json` unless `--replace` is explicit. The Core editing plan is written with `plan_meta.source = "ai_assisted"`, `approved_by_user = false` until `approve-plan`.

`compose` still enforces the existing voice/video duration compatibility. AI estimated duration is a planning suggestion, **not** the measured duration of generated media or voice.

## Offline mode (no API token usage)

For a 3-scene approved script whose scene IDs match the sample:

```powershell
ai-video-editor agent-scene-plan openai_api_test_001 --import-json .\examples\agent_scene_plan_sample.json
ai-video-editor agent-scene-approve openai_api_test_001
ai-video-editor agent-scene-export openai_api_test_001
```

The sample media types are 1 image + 2 video clips. If original script contains an image without narration, the proposal is rejected. Adjust `agent_scene_plan_sample.json` to match your actual script first. Use `--replace` only if you explicitly intend to overwrite a previous scene proposal or generated brief.

## Commands

| Command | Description |
|---|---|
| `agent-scene-plan PROJECT --model MODEL` | OpenAI Responses API with Structured Outputs (billable) |
| `agent-scene-plan PROJECT --import-json PATH` | Offline import of scene proposal |
| `agent-scene-status PROJECT` | Status and token counts |
| `agent-scene-approve PROJECT` | Lock approved content hash |
| `agent-scene-export PROJECT [--replace]` | Export Phase 2.1-compatible manual asset brief |
| `agent-scene-apply PROJECT [--replace]` | Write Core-compatible editing plan after storyboard is ready |

## Safety and boundaries

- Script must be approved and unchanged. Changing its contents invalidates the scene proposal.
- No media is generated or downloaded.
- The AI proposal is reviewed explicitly before export or apply.
- No silent replacement of existing editing plans; separate Core plan approval is still required.
- API credentials are read from `OPENAI_API_KEY` and never persisted.
- `gpt-6-luna` worked in the user's earlier Phase 2.2 test, but model availability and usage price depend on the API account.
- Image and video clips are the supported media proposal types in 2.3; image sequences can still be supplied manually via Phase 2.1.
