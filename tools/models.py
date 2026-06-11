"""Data models for the internal tool calling framework."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolDefinition:
    """Static metadata describing one callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    destructive: bool = False
    timeout_seconds: float = 10.0
    max_output_chars: int = 6000
    enabled_by_default: bool = True


@dataclass(frozen=True)
class Tool:
    """A registered tool definition and its local handler."""

    definition: ToolDefinition
    handler: ToolHandler


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation request."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    source: str = "deterministic"


@dataclass(frozen=True)
class ToolResult:
    """Normalized result for one tool invocation."""

    call_id: str
    name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "call_id": self.call_id,
            "name": self.name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "truncated": self.truncated,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ToolTrace:
    """Trace for a batch of tool calls."""

    calls: list[ToolCall]
    results: list[ToolResult]
    total_duration_ms: float
    failed_count: int
    success_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "created_at": call.created_at,
                    "source": call.source,
                }
                for call in self.calls
            ],
            "results": [result.to_dict() for result in self.results],
            "total_duration_ms": self.total_duration_ms,
            "failed_count": self.failed_count,
            "success_count": self.success_count,
        }


def truncate_text(text: Any, max_chars: int) -> tuple[str, bool]:
    """Convert output to text and truncate it for prompt safety."""

    value = text if isinstance(text, str) else repr(text)
    if max_chars < 0:
        max_chars = 0
    if len(value) <= max_chars:
        return value, False
    suffix = "\n...<truncated>"
    if max_chars <= len(suffix):
        return value[:max_chars], True
    return value[: max_chars - len(suffix)] + suffix, True

