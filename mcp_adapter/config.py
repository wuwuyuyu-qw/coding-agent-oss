"""Configuration and CLI argument parsing for the MCP server adapter."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from tools.safety import resolve_repo_root

from .errors import MCPConfigError


EXPOSED_SAFE_TOOLS = ("list_files", "read_file", "search_code", "grep_code", "git_diff")
TEST_TOOL_NAME = "run_tests"


@dataclass(frozen=True)
class MCPServerConfig:
    repo_root: Path
    transport: str = "stdio"
    enable_test_tool: bool = False
    max_output_chars: int = 12000
    timeout_seconds: float = 30.0

    @property
    def enabled_tools(self) -> list[str]:
        tools = list(EXPOSED_SAFE_TOOLS)
        if self.enable_test_tool:
            tools.append(TEST_TOOL_NAME)
        return tools


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the coding-agent-oss MCP server adapter.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    parser.add_argument("--enable-test-tool", action="store_true")
    parser.add_argument("--max-output-chars", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def parse_args(argv: list[str] | None = None) -> MCPServerConfig:
    args = build_arg_parser().parse_args(argv)
    return build_config(
        repo_root=args.repo_root,
        transport=args.transport,
        enable_test_tool=args.enable_test_tool,
        max_output_chars=args.max_output_chars,
        timeout_seconds=args.timeout_seconds,
    )


def build_config(
    *,
    repo_root: str,
    transport: str = "stdio",
    enable_test_tool: bool = False,
    max_output_chars: int = 12000,
    timeout_seconds: float = 30.0,
) -> MCPServerConfig:
    if transport != "stdio":
        raise MCPConfigError(f"unsupported_transport:{transport}")
    if max_output_chars <= 0:
        raise MCPConfigError("max_output_chars must be positive")
    if timeout_seconds <= 0:
        raise MCPConfigError("timeout_seconds must be positive")
    try:
        root = resolve_repo_root(repo_root)
    except Exception as exc:
        raise MCPConfigError(str(exc)) from exc
    return MCPServerConfig(
        repo_root=root,
        transport=transport,
        enable_test_tool=enable_test_tool,
        max_output_chars=max_output_chars,
        timeout_seconds=timeout_seconds,
    )
