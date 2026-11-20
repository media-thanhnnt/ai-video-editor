# Phase 1.4 — Rule-based Planner contract

**Scope:** deterministic offline planning, exact scene ID mapping, schema validation, immutable storyboard merge, optional integration with Phase 1.3 Timeline Engine. No media rendered, no AI API/token usage.

## Source of truth

- `storyboard.json` remains the canonical scene order, narration, and asset paths. Planner never writes to it.
- `config.json` supplies `planning.style` and `planning.seed` defaults; CLI can override without mutating config.
- Planner emits only the allow-listed fields: `scene_id`, `motion`, `motion_intensity`, `transition_to_next`, `transition_length`.
- The final scene must have `transition_to_next: none` and `transition_length: null`.
- All scene IDs must appear in exact storyboard order; duplicates, omissions, unexpected IDs, fields and enums are errors.
- `editing_plan.json` uses `schema_version: "1.1"` and `plan_meta.source: "rule_based"`. The output uses a reproducible, local PRNG seeded by signed 64-bit integer.
- `cache_key` covers current storyboard/config/style/seed and planner version. Modified inputs cause stale-plan rejection on `timeline --use-plan`. A cache key does **not** authenticate the plan contents; plan approval and integrity checks belong to later phases.
- Output `editing_plan.json` has no generation timestamp; identical inputs produce byte-identical files using atomic JSON replace.

## Style defaults

| style | motion choices | intensity | transition choices | length |
| --- | --- | --- | --- | --- |
| documentary | zoom_in, pan_left, zoom_out, pan_right, static | subtle, medium | crossfade, dissolve, none | medium |
| calm | static, zoom_in, zoom_out, pan_left, pan_right | subtle | crossfade, dissolve | long |
| energetic | pan_left, zoom_in, pan_right, zoom_out, static | medium, strong | none, crossfade | short |

Styles define allowed choices, not semantic content understanding. No image recognition; a pan on an image with insufficient horizontal crop room must later be clamped/fallback by the Render Engine (Phase 1.5) and recorded as warning.

## Python API

- `get_default_editing_plan(project, *, style=None, seed=None, write_output=True)`
- `validate_editing_plan(project, plan, *, require_current=True)`
- `merge_plan_into_storyboard(project, plan)` returns a new dict; does not modify original.
- `plan_cache_key(project, style, seed)`
- `build_timeline(project, ..., editing_plan=validated_plan)` uses each boundary's transition and annotates `video_track[].motion`; older no-plan API stays supported.

`timeline --use-plan` validates the existing plan before probing assets or overwriting the timeline. Other timeline flags `--transition`/`--length` are ignored for transition selection when a plan is selected; leave them unset for clarity.

## Known limitations

1. The planner makes pseudo-random, style-controlled choices, not semantic choices from an AI model (Phase 2).
2. Motion quality, valid pan crop range, and physical rendering are the concern of Phase 1.5.
3. `use_ai` in config is deliberately ignored by this offline Phase 1.4 command.
4. `timeline --use-plan` still validates real media with FFprobe/FFmpeg; `plan` itself does not require real media.
5. This is a local single-user workflow; concurrent writes to the same project are not supported.
