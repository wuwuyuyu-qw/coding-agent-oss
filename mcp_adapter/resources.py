"""Read-only MCP resource payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.safety import IGNORED_DIRS

from .config import MCPServerConfig
from .tool_adapter import MCPToolAdapter


def tool_catalog(adapter: MCPToolAdapter) -> str:
    payload = {
        "resources": ["repo://tool-catalog", "repo://summary"],
        "tools": adapter.list_tool_definitions(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def repo_summary(config: MCPServerConfig, adapter: MCPToolAdapter) -> str:
    payload: dict[str, Any] = {
        "repo_root": config.repo_root.name,
        "file_count": _count_repo_files(config.repo_root),
        "enabled_tools": [item["name"] for item in adapter.list_tool_definitions()],
        "safety_policy": {
            "repo_root_fixed": True,
            "path_traversal_blocked": True,
            "sensitive_files_blocked": True,
            "run_tests_requires_flag": True,
            "apply_patch_exposed": False,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def read_resource(uri: str, config: MCPServerConfig, adapter: MCPToolAdapter) -> str:
    if uri == "repo://tool-catalog":
        return tool_catalog(adapter)
    if uri == "repo://summary":
        return repo_summary(config, adapter)
    raise ValueError(f"Unknown MCP resource URI: {uri}")


def _count_repo_files(repo_root: Path) -> int:
    count = 0
    for root, dirnames, filenames in repo_root.walk():
        dirnames[:] = [dirname for dirname in dirnames if dirname not in IGNORED_DIRS]
        count += len(filenames)
    return count
