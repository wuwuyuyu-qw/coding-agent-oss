"""Adapter from MCP tool calls to the internal ToolExecutor."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from tools.builtin_tools import build_default_registry
from tools.executor import ToolExecutor
from tools.models import Tool, ToolCall, ToolResult
from tools.registry import ToolRegistry

from .config import EXPOSED_SAFE_TOOLS, MCPServerConfig, TEST_TOOL_NAME
from .schemas import tool_schema


class MCPToolAdapter:
    """Expose selected internal tools with a fixed server-side repo_root."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.registry = _build_registry(config)
        self.executor = ToolExecutor(self.registry)

    def list_tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for tool in self.registry.list_tools():
            if tool.definition.name not in self.config.enabled_tools:
                continue
            definitions.append(
                {
                    "name": tool.definition.name,
                    "description": tool.definition.description,
                    "input_schema": tool_schema(tool.definition.name),
                    "read_only": tool.definition.read_only,
                    "destructive": tool.definition.destructive,
                    "enabled": self.registry.is_enabled(tool.definition.name),
                }
            )
        return definitions

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if "repo_root" in args:
            return _blocked_repo_root(name)
        if name not in self.config.enabled_tools:
            return _disabled_or_hidden(name)

        args["repo_root"] = str(self.config.repo_root)
        result = self.executor.execute(
            ToolCall(name=name, arguments=args, source="mcp"),
        )
        return self._format_result(result)

    @staticmethod
    def _format_result(result: ToolResult) -> dict[str, Any]:
        return {
            "tool": result.name,
            "success": result.success,
            "content": result.output if result.success else "",
            "error": result.error if not result.success else "",
            "metadata": {
                "tool": result.name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
                "error": result.error if not result.success else "",
                **result.metadata,
            },
        }


def _build_registry(config: MCPServerConfig) -> ToolRegistry:
    source = build_default_registry()
    tools: list[Tool] = []
    for tool in source.list_tools():
        name = tool.definition.name
        if name not in (*EXPOSED_SAFE_TOOLS, TEST_TOOL_NAME):
            continue
        enabled = name != TEST_TOOL_NAME or config.enable_test_tool
        definition = replace(
            tool.definition,
            timeout_seconds=min(tool.definition.timeout_seconds, config.timeout_seconds),
            max_output_chars=min(tool.definition.max_output_chars, config.max_output_chars),
            enabled_by_default=enabled,
        )
        tools.append(Tool(definition=definition, handler=tool.handler))
    registry = ToolRegistry(tools)
    if not config.enable_test_tool and registry.has_tool(TEST_TOOL_NAME):
        registry.disable_tool(TEST_TOOL_NAME)
    return registry


def _blocked_repo_root(name: str) -> dict[str, Any]:
    return {
        "tool": name,
        "success": False,
        "content": "",
        "error": "MCP tool input must not include repo_root; repo_root is fixed by server config",
        "metadata": {
            "tool": name,
            "success": False,
            "duration_ms": 0.0,
            "truncated": False,
            "error": "repo_root_override_not_allowed",
            "error_type": "MCPInputError",
        },
    }


def _disabled_or_hidden(name: str) -> dict[str, Any]:
    return {
        "tool": name,
        "success": False,
        "content": "",
        "error": f"Tool disabled or not exposed over MCP: {name}",
        "metadata": {
            "tool": name,
            "success": False,
            "duration_ms": 0.0,
            "truncated": False,
            "error": "tool_disabled_or_not_exposed",
            "error_type": "MCPToolAccessError",
        },
    }
