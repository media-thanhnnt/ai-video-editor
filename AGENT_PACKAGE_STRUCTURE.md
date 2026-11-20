# Phase 2.1 — Agent package restructure

## Why the `agent/` directory was missing

The previous Phase 2.1 release placed all agent logic in `src/ai_video_editor/agent.py`.
This release replaces the single module with a Python package, preserving the same public import API and CLI commands.

## Source layout

```text
src/ai_video_editor/
├── __init__.py
├── cli.py
├── project.py
├── assets.py
├── planner.py
├── timeline.py
├── renderer.py
├── audio.py
├── qa.py
├── ...
└── agent/
    ├── __init__.py       # backward-compatible public imports
    ├── manifest.py       # brief/manifest, binding, approval, compile
    └── tool_registry.py  # tool allowlist and handlers
```

## Installation / upgrade (PowerShell)

1. Back up your repository and preserve `projects/`.
2. Copy the ZIP content into the repository, replacing source as prompted.
3. **Remove the old `src/ai_video_editor/agent.py`** if it still exists.
   A file and package with the same import name must not be left side by side.
4. Run `py -m pip install -e .` in the repository root.
5. Run `py -m unittest tests.test_agent_phase21 tests.test_agent_package_layout -v` (or discovery if `tests` isn't a package).
6. Run `py -m unittest discover -s tests -v` and `ai-video-editor agent-tools`.

This release does **not** change the Phase 1 Core, CLI names, or `agent_manifest.json` format.

## Current functionality

`manifest.py` contains manifest creation, asset binding, approval, and storyboard compilation.
`tool_registry.py` provides the allowlisted tool abstraction.
**This is the Phase 2.1 foundation, not yet a self-directed LLM Agent.**
Future Phase 2 modules can be added under the same `agent/` package.
