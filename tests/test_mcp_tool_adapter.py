from __future__ import annotations

from pathlib import Path

from mcp_adapter.config import build_config
from mcp_adapter.tool_adapter import MCPToolAdapter


def test_list_files_maps_to_internal_tool_call(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("list_files", {"max_results": 10})

    assert result["success"] is True
    assert "app.py" in result["content"]
    assert result["metadata"]["tool"] == "list_files"


def test_read_file_maps_to_internal_tool_call(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("line1\nline2\n", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("read_file", {"path": "app.py", "start_line": 2})

    assert result["success"] is True
    assert "line2" in result["content"]


def test_search_code_reuses_existing_tool_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def target():\n    return 42\n", encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("search_code", {"query": "target function", "top_k": 3})

    assert result["success"] is True
    assert "target" in result["content"]


def test_tool_failure_returns_structured_error(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("grep_code", {"pattern": "["})

    assert result["success"] is False
    assert result["metadata"]["error_type"]


def test_long_tool_output_is_truncated(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 5000, encoding="utf-8")
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path), max_output_chars=200))

    result = adapter.call_tool("read_file", {"path": "big.txt", "max_chars": 5000})

    assert result["success"] is True
    assert result["metadata"]["truncated"] is True
    assert len(result["content"]) <= 220


def test_mcp_input_cannot_override_repo_root(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    result = adapter.call_tool("list_files", {"repo_root": "/"})

    assert result["success"] is False
    assert result["metadata"]["error"] == "repo_root_override_not_allowed"


def test_apply_patch_is_not_exposed(tmp_path: Path) -> None:
    adapter = MCPToolAdapter(build_config(repo_root=str(tmp_path)))

    names = {item["name"] for item in adapter.list_tool_definitions()}
    result = adapter.call_tool("apply_patch", {})

    assert "apply_patch" not in names
    assert result["success"] is False
