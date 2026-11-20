# AI Video Editor — Phase 2.1

Builds on **Core 1.10.3** without changing its rendering/audio/subtitle/QA modules.

## Windows PowerShell quick start

Run from the repository root after extracting this release; preserve existing `projects/`.

```powershell
py -m pip install -e .
py -m unittest tests.test_agent_phase21 -v
ai-video-editor init agent_demo --title "Khám phá đại dương"
ai-video-editor agent-init agent_demo --brief .\examples\agent_brief_manual.json
ai-video-editor agent-status agent_demo
ai-video-editor agent-tools
```

Generate media yourself in ChatGPT/Gemini/TTS, then save:

```text
projects/agent_demo/assets/images/scene_001.png
projects/agent_demo/assets/audio/scene_001.wav
projects/agent_demo/assets/videos/scene_002.mp4
```

Bind files, approve and compile:

```powershell
ai-video-editor agent-bind agent_demo --scene 001 --kind media --file assets/images/scene_001.png
ai-video-editor agent-bind agent_demo --scene 001 --kind voice --file assets/audio/scene_001.wav
ai-video-editor agent-bind agent_demo --scene 002 --kind media --file assets/videos/scene_002.mp4
ai-video-editor agent-status agent_demo
ai-video-editor agent-approve agent_demo
ai-video-editor agent-compile agent_demo
ai-video-editor assets agent_demo
ai-video-editor compose agent_demo --deep
```

If your project requires editing-plan approval, run `ai-video-editor plan agent_demo` and `ai-video-editor approve-plan agent_demo` before `compose`.

**This release is Phase 2.1 foundation, not an LLM-powered planning agent yet.** There is no new API cost. For manual generation, chatbot provider subscriptions and usage caps apply separately.


## Updated source layout
Agent code is now organized under `src/ai_video_editor/agent/` (`manifest.py`, `tool_registry.py`, `__init__.py`). See `AGENT_PACKAGE_STRUCTURE.md`. Remove the obsolete `src/ai_video_editor/agent.py` when upgrading.
