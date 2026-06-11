from __future__ import annotations

from tools.context_builder import ToolContextBuilder
from tools.models import ToolResult


def test_builds_context_for_success_and_failure_results() -> None:
    results = [
        ToolResult(call_id="1", name="list_files", success=True, output="app.py", duration_ms=12),
        ToolResult(
            call_id="2",
            name="read_file",
            success=False,
            error="ToolSafetyError: blocked",
            duration_ms=3,
        ),
    ]

    context = ToolContextBuilder(max_context_chars=1000).build_context(results=results)

    assert "[Tool Calling Context]" in context
    assert "tool: list_files" in context
    assert "success: true" in context
    assert "summary:" in context
    assert "tool: read_file" in context
    assert "success: false" in context
    assert "ToolSafetyError" in context


def test_context_builder_respects_max_context_chars() -> None:
    results = [
        ToolResult(call_id="1", name="large", success=True, output="x" * 500, duration_ms=1)
    ]

    context = ToolContextBuilder(max_context_chars=120, max_output_chars=1000).build_context(
        results=results
    )

    assert len(context) <= 120
    assert "tool: large" in context

