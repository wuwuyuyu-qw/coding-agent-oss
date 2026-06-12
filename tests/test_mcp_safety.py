from __future__ import annotations

from pathlib import Path

from mcp_adapter.config import build_config
from mcp_adapter.resources import read_resource
from mcp_adapter.tool_adapter import MCPToolAdapter


def test_read_file_path_traversal_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("read_file", {"path": "../outside.txt"})

    assert result["success"] is False
    assert "Path escapes repo_root" in result["error"]


def test_read_file_env_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("read_file", {"path": ".env"})

    assert result["success"] is False
    assert "sensitive path" in result["error"]


def test_read_file_git_config_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("read_file", {"path": ".git/config"})

    assert result["success"] is False
    assert "sensitive path" in result["error"]


def test_run_tests_disabled_by_default(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("run_tests", {"command": "pytest"})

    assert result["success"] is False
    assert "disabled" in result["error"]


def test_run_tests_enabled_still_uses_allowlist(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path), enable_test_tool=True))

    result = adapter.call_tool("run_tests", {"command": "python -c \"print(1)\""})

    assert result["success"] is False
    assert "allowlisted" in result["error"]


def test_tool_catalog_resource_lists_enabled_tools(tmp_path: Path) -> None:
    config = build_config(repo_root=str(tmp_path), enable_test_tool=True)
    adapter = MCPToolAdapter(config)

    payload = read_resource("repo://tool-catalog", config, adapter)

    assert "list_files" in payload
    assert "run_tests" in payload
    assert "apply_patch" not in payload
