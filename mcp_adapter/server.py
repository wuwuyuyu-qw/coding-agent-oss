"""Minimal stdio MCP server exposing the internal tool framework."""

from __future__ import annotations

import json
import sys
from typing import Any

from .config import MCPServerConfig, parse_args
from .errors import MCPDependencyError
from .resources import read_resource
from .tool_adapter import MCPToolAdapter


def import_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise MCPDependencyError(
            "MCP SDK is not installed. Install project requirements or `pip install mcp` to run the server."
        ) from exc
    return FastMCP


def build_server(config: MCPServerConfig) -> Any:
    FastMCP = import_fastmcp()
    app = FastMCP("coding-agent-oss")
    adapter = MCPToolAdapter(config)

    for definition in adapter.list_tool_definitions():
        name = definition["name"]

        async def _tool(arguments: dict[str, Any] | None = None, *, _name: str = name) -> str:
            return json.dumps(adapter.call_tool(_name, arguments or {}), indent=2, ensure_ascii=False)

        app.tool(name=name, description=definition["description"])(_tool)

    @app.resource("repo://tool-catalog")
    def _tool_catalog() -> str:
        return read_resource("repo://tool-catalog", config, adapter)

    @app.resource("repo://summary")
    def _repo_summary() -> str:
        return read_resource("repo://summary", config, adapter)

    return app


def main(argv: list[str] | None = None) -> int:
    try:
        config = parse_args(argv)
        server = build_server(config)
        server.run(transport=config.transport)
        return 0
    except Exception as exc:
        print(f"[MCP Server Error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised via CLI smoke test.
    raise SystemExit(main())
