from __future__ import annotations

import pytest

from tools.errors import ToolDisabledError, ToolNotFoundError
from tools.models import Tool, ToolDefinition
from tools.registry import ToolRegistry


def _tool(name: str = "echo") -> Tool:
    return Tool(
        definition=ToolDefinition(name=name, description="Echo input"),
        handler=lambda args: args.get("text", ""),
    )


def test_register_and_get_tool() -> None:
    registry = ToolRegistry()
    registry.register(_tool())

    assert registry.has_tool("echo")
    assert registry.get("echo").definition.name == "echo"


def test_missing_tool_raises_clear_error() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError, match="Tool not found"):
        registry.get("missing")


def test_disable_and_enable_tool() -> None:
    registry = ToolRegistry([_tool()])

    registry.disable_tool("echo")
    assert not registry.is_enabled("echo")
    with pytest.raises(ToolDisabledError):
        registry.get("echo")

    registry.enable_tool("echo")
    assert registry.get("echo").definition.name == "echo"


def test_duplicate_registration_overwrites() -> None:
    registry = ToolRegistry([_tool("echo")])
    registry.register(
        Tool(
            definition=ToolDefinition(name="echo", description="New echo"),
            handler=lambda args: "new",
        )
    )

    assert registry.get("echo").handler({}) == "new"
