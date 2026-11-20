# AI Video Editor — Phase 2.2: AI Requirement & Script Generator

Implemented on top of **accepted Phase 2.1 + Core 1.10.3** (same Python repository, no project migration).

## Features

- Generate a structured video script from a Vietnamese/English natural-language request using **OpenAI Responses API**, JSON Schema, and explicit `OPENAI_API_KEY`.
- **Offline manual mode**: prepare JSON with your existing chatbot subscription; import via `--import-json` (no API usage).
- Validate script, planned scene narrations, visual prompts, rough durations; do not invent asset filenames.
- Review and explicitly approve the script; SHA-256 approval invalidates if the draft changes.
- Export an **existing Phase 2.1-compatible brief**. Continue Phase 2.1 manual asset creation with `agent-init`, `agent-bind`, `agent-approve`, `agent-compile`, `compose`.
- Log provider-reported token usage; no speculative costs. `estimated_cost_usd=null` for OpenAI (cost depends on current API pricing/model).
- No API calls during import/tests and no media generation. No API keys stored in project files.

## Install on Windows (PowerShell)

Extract over the existing source, **preserving `projects/`**, then:

```powershell
py -m pip install -e .
py -m unittest discover -s tests -p "test_agent*.py" -v
```

## Path A — existing ChatGPT/Gemini subscription, no API key

1. Ask your chatbot to generate a JSON script using `examples/agent_script_sample.json` as the schema example.
2. Save the result as `script.json` (the app validates content).
3. Run:

```powershell
ai-video-editor init ocean_ai_001 --title "Ocean AI"    # skip if it already exists
ai-video-editor agent-script ocean_ai_001 --import-json .\script.json
ai-video-editor agent-script-status ocean_ai_001
# Review projects/ocean_ai_001/agent_script_draft.json, edit if necessary
ai-video-editor agent-script-approve ocean_ai_001
ai-video-editor agent-script-export ocean_ai_001
ai-video-editor agent-init ocean_ai_001 --brief .\projects\ocean_ai_001\agent_brief_generated.json
ai-video-editor agent-status ocean_ai_001
```

Then bind manual assets according to Phase 2.1 instructions. **Script export does not generate media or replace an existing Agent manifest**.

## Path B — OpenAI API, token charges apply

Do not store API keys in code, project files, Git or shell history. Set an environment variable in your session using a secure method (example only):

```powershell
$env:OPENAI_API_KEY = Read-Host "OpenAI API key" -AsSecureString | ForEach-Object { [System.Net.NetworkCredential]::new('', $_).Password }
ai-video-editor agent-script ocean_ai_001 --request "Tạo video 2 phút bằng tiếng Việt về đại dương, phong cách documentary, khoảng 8 scene" --model gpt-4.1-mini
ai-video-editor agent-script-status ocean_ai_001
```

You can also use `--request-file .\video_request.txt` for longer requirements. `--replace` is required to overwrite an existing draft; approval is reset in that case. The model parameter must be a valid model available in **your** API account. The API call is made only by `agent-script` without `--import-json`. API token usage is reported by provider, not estimated from prose.

## Stored project files

```
projects/ocean_ai_001/
  agent_script_draft.json       # draft + usage + approval hash (not an API key)
  agent_brief_generated.json    # Phase 2.1-compatible plan (only after approval)
  agent_manifest.json           # created by Phase 2.1 agent-init
```

## Safety, accuracy and limitations

- LLM can hallucinate facts. Review factual scripts, copyright and source references manually before publishing.
- The generated estimated scene durations are **not** accurate frame/sample timing; existing Core 1.10.3 computes timing from actual imported media and voice.
- Image scenes need narration and voice; `agent-init` cannot compile until your assets are bound.
- Script editing after approval requires reapproval; export checks SHA-256.
- Large generated scripts may exceed model output limits. Adjust brief, model or divide into shorter videos.
- Real OpenAI API connectivity and model permissions cannot be tested without a user API key; unit tests mock the network.
