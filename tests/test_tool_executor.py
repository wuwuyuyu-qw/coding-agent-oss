from __future__ import annotations

import time

from tools.executor import ToolExecutor
from tools.models import Tool, ToolCall, ToolDefinition
from tools.registry import ToolRegistry


def test_execute_successful_mock_tool() -> None:
    registry = ToolRegistry(
        [
            Tool(
                definition=ToolDefinition(name="echo", description="Echo"),
                handler=lambda args: f"hello {args['name']}",
            )
        ]
    )

    result = ToolExecutor(registry).execute(
        ToolCall(name="echo", arguments={"name": "agent"})
    )

    assert result.success is True
    assert result.output == "hello agent"
    assert result.to_dict()["name"] == "echo"


def test_tool_exception_returns_failure_result() -> None:
    def boom(_args):
        raise RuntimeError("bad tool")

    registry = ToolRegistry(
        [Tool(definition=ToolDefinition(name="boom", description="Boom"), handler=boom)]
    )

    result = ToolExecutor(registry).execute(ToolCall(name="boom"))

    assert result.success is False
    assert "RuntimeError" in result.error
    assert "bad tool" in result.error


def test_long_output_is_truncated() -> None:
    registry = ToolRegistry(
        [
            Tool(
                definition=ToolDefinition(
                    name="large",
                    description="Large",
                    max_output_chars=20,
                ),
                handler=lambda _args: "x" * 100,
            )
        ]
    )

    result = ToolExecutor(registry).execute(ToolCall(name="large"))

    assert result.success is True
    assert result.truncated is True
    assert len(result.output) <= 20


def test_disabled_tool_does_not_execute() -> None:
    called = False

    def handler(_args):
        nonlocal called
        called = True
        return "should not happen"

    registry = ToolRegistry(
        [Tool(definition=ToolDefinition(name="off", description="Off"), handler=handler)]
    )
    registry.disable_tool("off")

    result = ToolExecutor(registry).execute(ToolCall(name="off"))

    assert result.success is False
    assert "ToolDisabledError" in result.error
    assert called is False


def test_timeout_returns_failure_result_quickly() -> None:
    def slow(_args):
        time.sleep(1)
        return "late"

    registry = ToolRegistry(
        [
            Tool(
                definition=ToolDefinition(
                    name="slow",
                    description="Slow",
                    timeout_seconds=0.01,
                ),
                handler=slow,
            )
        ]
    )

    started = time.perf_counter()
    result = ToolExecutor(registry).execute(ToolCall(name="slow"))

    assert result.success is False
    assert "ToolTimeoutError" in result.error
    assert time.perf_counter() - started < 0.5

