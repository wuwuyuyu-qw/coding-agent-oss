"""JSON-schema helpers for MCP tool registration."""

from __future__ import annotations

from typing import Any


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_files": {
        "type": "object",
        "properties": {
            "max_results": {"type": "integer", "default": 200},
            "include_hidden": {"type": "boolean", "default": False},
            "extensions": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "max_chars": {"type": "integer"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "search_code": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "error_log": {"type": "string"},
            "failing_tests": {"type": "array", "items": {"type": "string"}},
            "target_file": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "grep_code": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "max_results": {"type": "integer"},
            "extensions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "git_diff": {
        "type": "object",
        "properties": {"max_chars": {"type": "integer"}},
        "additionalProperties": False,
    },
    "run_tests": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "number"},
            "max_output_chars": {"type": "integer"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def tool_schema(name: str) -> dict[str, Any]:
    return TOOL_SCHEMAS.get(name, {"type": "object", "properties": {}, "additionalProperties": False})
