"""Explicit finite tool registry for AI research tasks."""

from __future__ import annotations

from .models import HarnessTool


class ToolRegistry:
    def __init__(self, tools: tuple[HarnessTool, ...] = ()) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> HarnessTool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool
