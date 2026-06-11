"""Prompt context builder for tool calling results."""

from __future__ import annotations

from .models import ToolResult, ToolTrace, truncate_text


class ToolContextBuilder:
    """Build bounded prompt context from tool results."""

    def __init__(self, *, max_context_chars: int = 8000, max_output_chars: int = 2000) -> None:
        self.max_context_chars = max_context_chars
        self.max_output_chars = max_output_chars

    def build_context(self, trace: ToolTrace | None = None, results: list[ToolResult] | None = None) -> str:
        tool_results = results if results is not None else (trace.results if trace else [])
        if not tool_results:
            return ""

        context = "[Tool Calling Context]"
        for index, result in enumerate(tool_results, start=1):
            block = self._format_result(index, result)
            if len(context) + len(block) + 2 > self.max_context_chars:
                remaining = self.max_context_chars - len(context) - 2
                if remaining > 0:
                    clipped, _ = truncate_text(block, remaining)
                    context = f"{context}\n\n{clipped}"
                break
            context = f"{context}\n\n{block}"
        return context[: self.max_context_chars]

    def _format_result(self, index: int, result: ToolResult) -> str:
        lines = [
            f"[Tool Call {index}]",
            f"tool: {result.name}",
            f"success: {str(result.success).lower()}",
            f"duration_ms: {result.duration_ms:.2f}",
        ]
        if result.success:
            output, truncated = truncate_text(result.output, self.max_output_chars)
            lines.extend(["summary:", output])
            if result.truncated or truncated:
                lines.append("truncated: true")
        else:
            error, truncated = truncate_text(result.error, self.max_output_chars)
            lines.extend(["error:", error])
            if result.truncated or truncated:
                lines.append("truncated: true")
        return "\n".join(lines)

