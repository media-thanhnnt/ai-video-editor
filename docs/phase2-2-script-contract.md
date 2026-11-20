# Phase 2.2 — Requirement and Script Contract

- CLI: `agent-script`, `agent-script-status`, `agent-script-approve`, `agent-script-export`.
- Draft file: `projects/<id>/agent_script_draft.json` (`schema_version=2.2`). Contains `requirement`, `content`, `usage`, approval SHA-256. `OPENAI_API_KEY` never stored.
- Output brief: `projects/<id>/agent_brief_generated.json`, valid for Phase 2.1 `agent-init`.
- Model output schema fields: `title`, `language`, `style`, `target_duration_seconds`, `summary`, `script`, and `scenes[]` with `scene_id`, `type`, `narration`, `prompt`, `estimated_duration_seconds`.
- Allowed media scene types for AI scripts: `image`, `video_clip`; image scenes require nonempty narration. `image_sequence` requires frame-directory specification and is not automatically requested in Phase 2.2.
- Approval: the export requires `SCRIPT_APPROVED` and SHA-256 match of `content`. Editing after approval requires approving again.
- Calls to OpenAI API are explicitly initiated by `agent-script --request` or `--request-file`. `--import-json` is completely offline. Abort if draft exists before making a potentially billable API request, unless `--replace`.
- OpenAI Responses: POST `https://api.openai.com/v1/responses` using strict `text.format.json_schema`; `store=false`, timeout, error wrapping; usage from response; output verified by `validate_script`.
- Duration estimates are advisory. Core Phase 1.10.3 timing is derived from actual voice/media files after Phase 2.1 binding.
- Do not automatically overwrite Core `storyboard.json`; follow Phase 2.1 manifest, asset binding and approval flow.
- Cost is reported as `estimated_cost_usd=null` for online requests unless model pricing is independently configured. Tokens are provider-reported.
