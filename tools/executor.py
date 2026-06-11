"""Tool execution with timeout, truncation, and fail-open results."""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any

from .errors import ToolDisabledError, ToolNotFoundError, ToolSafetyError
from .models import ToolCall, ToolResult, ToolTrace, truncate_text
from .registry import ToolRegistry


class ToolExecutor:
    """Execute registered tools and normalize failures into ToolResult."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        allow_destructive: bool = False,
    ) -> None:
        self.registry = registry
        self.allow_destructive = allow_destructive

    def execute(self, tool_call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = self.registry.get(tool_call.name)
            if tool.definition.destructive and not self.allow_destructive:
                raise ToolSafetyError(f"Destructive tool is not allowed: {tool_call.name}")

            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(tool.handler, dict(tool_call.arguments))
                output = future.result(timeout=tool.definition.timeout_seconds)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

            text, truncated = truncate_text(output, tool.definition.max_output_chars)
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                output=text,
                duration_ms=_elapsed_ms(started),
                truncated=truncated,
            )
        except concurrent.futures.TimeoutError:
            return self._failure(
                tool_call,
                started,
                "ToolTimeoutError",
                f"Tool timed out: {tool_call.name}",
            )
        except (ToolNotFoundError, ToolDisabledError, ToolSafetyError) as exc:
            return self._failure(tool_call, started, type(exc).__name__, str(exc))
        except Exception as exc:
            return self._failure(tool_call, started, type(exc).__name__, str(exc))

    def execute_many(self, tool_calls: list[ToolCall]) -> ToolTrace:
        started = time.perf_counter()
        results = [self.execute(tool_call) for tool_call in tool_calls]
        success_count = sum(1 for result in results if result.success)
        failed_count = len(results) - success_count
        return ToolTrace(
            calls=tool_calls,
            results=results,
            total_duration_ms=_elapsed_ms(started),
            failed_count=failed_count,
            success_count=success_count,
        )

    def _failure(
        self,
        tool_call: ToolCall,
        started: float,
        error_type: str,
        message: str,
    ) -> ToolResult:
        error, truncated = truncate_text(f"{error_type}: {message}", 2000)
        return ToolResult(
            call_id=tool_call.call_id,
            name=tool_call.name,
            success=False,
            error=error,
            duration_ms=_elapsed_ms(started),
            truncated=truncated,
            metadata={"error_type": error_type},
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
