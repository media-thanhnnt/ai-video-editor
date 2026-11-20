# Phase 1.3 Functional Contract — Timeline Engine

## Source of truth

- Storyboard scene array controls scene order, media mapping and narration.
- Asset Manager's fresh `file_results[scene.audio].info.decoded_samples` controls voice duration.
- Config controls frame rate, PCM timeline sample rate, padding, min scene length, transitions, fade policy.
- Values in seconds are display only. Frame/sample indices are authoritative.

## Invariants

- First video scene starts at frame 0; each visual scene lasts positive frames.
- Each next scene starts at the previous scene end minus transition overlap.
- Transition overlap is bounded by `tail[i] + head[i+1]` and two adjacent overlaps may not consume the intervening scene.
- Every narration voice remains fully inside its visual scene and has no sample overlap with its neighbors.
- No scene can be dropped because a transition is too long; clamp/warn or fail.
- No output file is published if asset or timeline validation fails.
- JSON includes `schema_version=1.1`, `phase=1.3`, `input_hashes` and warnings.

## Intentional limitations

- One provisional transition style is applied to all boundaries, not AI-driven or scene-specific yet.
- No real `music_track`, subtitle placement, motion parameters, render, or per-frame audio generation in Phase 1.3.
- `validate_timeline()` checks internal geometry; it does not independently reopen media or validate hashes. Fresh Asset Manager validation happens in `build_timeline()`.
- The audio resampling result from Asset Manager is used as the precise source sample length; actual audio placement belongs to Phase 1.6.
- Output audio total length in AAC/MP4 may differ because of encoder priming; measured QA tolerance is a later phase.

## Failure handling

- Missing/invalid audio image -> `TimelineError` and unchanged previous `timeline.json`.
- Transition violating limits with `fail` policy -> error; with `clamp_and_warn` -> warning and shorter overlap.
- Outro fade longer than final tail: fail or clamp/warn.
- Intro fade longer than head: warning `INTRO_OVER_VOICE` (fade affects video only).
