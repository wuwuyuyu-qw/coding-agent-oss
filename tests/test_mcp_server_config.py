from __future__ import annotations

from pathlib import Path

import pytest

from mcp_adapter.config import build_config, parse_args
from mcp_adapter.errors import MCPConfigError, MCPDependencyError
from mcp_adapter.server import import_fastmcp


def test_valid_repo_root_config_succeeds(tmp_path: Path) -> None:
    config = build_config(repo_root=str(tmp_path))

    assert config.repo_root == tmp_path.resolve()
    assert "list_files" in config.enabled_tools
    assert "run_tests" not in config.enabled_tools


def test_missing_repo_root_config_fails(tmp_path: Path) -> None:
    with pytest.raises(MCPConfigError):
        build_config(repo_root=str(tmp_path / "missing"))


def test_enabled_tools_include_run_tests_only_when_enabled(tmp_path: Path) -> None:
    config = build_config(repo_root=str(tmp_path), enable_test_tool=True)

    assert "run_tests" in config.enabled_tools
    assert "apply_patch" not in config.enabled_tools


def test_stdio_transport_args_parse(tmp_path: Path) -> None:
    config = parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--transport",
            "stdio",
            "--enable-test-tool",
            "--max-output-chars",
            "5000",
            "--timeout-seconds",
            "5",
        ]
    )

    assert config.transport == "stdio"
    assert config.enable_test_tool is True
    assert config.max_output_chars == 5000
    assert config.timeout_seconds == 5


def test_missing_mcp_sdk_is_reported_cleanly() -> None:
    try:
        fastmcp = import_fastmcp()
    except MCPDependencyError as exc:
        assert "MCP SDK is not installed" in str(exc)
    else:
        assert fastmcp is not None
