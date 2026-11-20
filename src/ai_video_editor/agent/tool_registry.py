"""Allowlisted agent tools (Phase 2.1). No arbitrary shell execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..project import ValidationError
from .manifest import (create_manifest, bind_asset, approve_manifest, compile_storyboard)

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]


class ToolRegistry:
    """Fixed allowlist: never execute shell commands or arbitrary Python supplied by a model."""
    def __init__(self) -> None:
        self._tools: dict[str,Tool]={}

    def register(self, tool: Tool) -> None:
        if not tool.name.isidentifier() or tool.name in self._tools:
            raise ValidationError('Invalid/duplicate tool name')
        self._tools[tool.name]=tool

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise ValidationError(f'Unknown agent tool: {name}')
        return self._tools[name].handler(**kwargs)

    def describe(self) -> list[dict[str,str]]:
        return [{'name':t.name,'description':t.description} for t in self._tools.values()]


def registry() -> ToolRegistry:
    tools=ToolRegistry()
    for name,description,handler in [
        ('create_manifest','Create reviewable asset requests from a structured brief',create_manifest),
        ('bind_asset','Bind a locally supplied asset to a scene',bind_asset),
        ('approve_manifest','Approve prepared plan and assets',approve_manifest),
        ('compile_storyboard','Compile approved manifest for the existing Core',compile_storyboard),
    ]:
        tools.register(Tool(name,description,handler))
    return tools
