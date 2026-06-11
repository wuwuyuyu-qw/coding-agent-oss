from __future__ import annotations

import json
from pathlib import Path

from tools.builtin_tools import build_default_registry
from tools.executor import ToolExecutor
from tools.models import ToolCall


def _execute(name: str, arguments: dict) -> tuple[object, object]:
    registry = build_default_registry()
    result = ToolExecutor(registry).execute(ToolCall(name=name, arguments=arguments))
    payload = json.loads(result.output) if result.output else {}
    return result, payload


def test_list_files_ignores_common_directories(tmp_path: Path) -> None:
    for dirname in [".git", "venv", "node_modules", "target"]:
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "ignored.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")

    result, payload = _execute("list_files", {"repo_root": str(tmp_path)})

    assert result.success is True
    assert payload["files"] == ["src/app.py"]


def test_read_file_reads_line_range(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result, payload = _execute(
        "read_file",
        {"repo_root": str(tmp_path), "path": "app.py", "start_line": 2, "end_line": 3},
    )

    assert result.success is True
    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert payload["content"] == "two\nthree"


def test_read_file_blocks_path_traversal(tmp_path: Path) -> None:
    result = ToolExecutor(build_default_registry()).execute(
        ToolCall(
            name="read_file",
            arguments={"repo_root": str(tmp_path), "path": "../secret.txt"},
        )
    )

    assert result.success is False
    assert "Path escapes repo_root" in result.error


def test_search_code_reuses_rag_retrieval(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "def divide(a, b):\n    return a / b\n", encoding="utf-8"
    )

    result, payload = _execute(
        "search_code",
        {"repo_root": str(tmp_path), "query": "divide by zero", "top_k": 1},
    )

    assert result.success is True
    assert payload["results"][0]["file_path"] == "calculator.py"
    assert payload["results"][0]["symbol_name"] == "divide"


def test_grep_code_returns_matching_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("alpha\nneedle here\n", encoding="utf-8")

    result, payload = _execute(
        "grep_code",
        {"repo_root": str(tmp_path), "pattern": "needle", "extensions": [".py"]},
    )

    assert result.success is True
    assert payload["matches"][0]["line_number"] == 2
    assert "needle" in payload["matches"][0]["line"]


def test_grep_code_invalid_regex_returns_failure(tmp_path: Path) -> None:
    result = ToolExecutor(build_default_registry()).execute(
        ToolCall(name="grep_code", arguments={"repo_root": str(tmp_path), "pattern": "["})
    )

    assert result.success is False
    assert "unterminated character set" in result.error


def test_git_diff_non_git_repo_fails_gracefully(tmp_path: Path) -> None:
    result = ToolExecutor(build_default_registry()).execute(
        ToolCall(name="git_diff", arguments={"repo_root": str(tmp_path)})
    )

    assert result.success is False
    assert "not a git repository" in result.error


def test_run_tests_rejects_non_allowlisted_command(tmp_path: Path) -> None:
    result = ToolExecutor(build_default_registry()).execute(
        ToolCall(name="run_tests", arguments={"repo_root": str(tmp_path), "command": "rm -rf ."})
    )

    assert result.success is False
    assert "not allowlisted" in result.error


def test_run_tests_executes_safe_unittest_command(tmp_path: Path) -> None:
    (tmp_path / "test_sample.py").write_text(
        "import unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(1 + 1, 2)\n",
        encoding="utf-8",
    )

    result, payload = _execute(
        "run_tests",
        {
            "repo_root": str(tmp_path),
            "command": "python -m unittest discover -s .",
            "timeout_seconds": 10,
        },
    )

    assert result.success is True
    assert payload["exit_code"] == 0
    assert "Ran 1 test" in payload["stderr"]

