"""Tool registry for named local tools."""

from __future__ import annotations

from .errors import ToolDisabledError, ToolNotFoundError
from .models import Tool


class ToolRegistry:
    """Register, look up, enable, and disable tools.

    Registering a tool with an existing name intentionally overwrites the prior
    entry. This keeps tests and local composition simple and is documented in
    docs/tool_calling_framework.md.
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.name] = tool
        if not tool.definition.enabled_by_default:
            self._disabled.add(tool.definition.name)
        else:
            self._disabled.discard(tool.definition.name)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool not found: {name}")
        if name in self._disabled:
            raise ToolDisabledError(f"Tool disabled: {name}")
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def disable_tool(self, name: str) -> None:
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool not found: {name}")
        self._disabled.add(name)

    def enable_tool(self, name: str) -> None:
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool not found: {name}")
        self._disabled.discard(name)

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

