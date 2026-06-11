"""Builtin deterministic tools for the internal tool calling framework."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rag.hybrid_retriever import HybridRetriever

from .errors import ToolSafetyError
from .models import Tool, ToolDefinition, truncate_text
from .registry import ToolRegistry
from .safety import IGNORED_DIRS, is_ignored_path, resolve_repo_path, resolve_repo_root


def build_default_registry() -> ToolRegistry:
    """Return a registry containing all builtin tools."""

    return ToolRegistry(
        [
            Tool(
                definition=ToolDefinition(
                    name="list_files",
                    description="List repository files with ignored directories filtered out.",
                    input_schema={
                        "repo_root": "str",
                        "max_results": "int?",
                        "include_hidden": "bool?",
                        "extensions": "list[str]?",
                    },
                    read_only=True,
                    timeout_seconds=5,
                    max_output_chars=8000,
                ),
                handler=list_files,
            ),
            Tool(
                definition=ToolDefinition(
                    name="read_file",
                    description="Read a repo-local file or line range.",
                    input_schema={
                        "repo_root": "str",
                        "path": "str",
                        "start_line": "int?",
                        "end_line": "int?",
                        "max_chars": "int?",
                    },
                    read_only=True,
                    timeout_seconds=5,
                    max_output_chars=12000,
                ),
                handler=read_file,
            ),
            Tool(
                definition=ToolDefinition(
                    name="search_code",
                    description="Search repository code using the repo-level RAG retriever.",
                    input_schema={
                        "repo_root": "str",
                        "query": "str",
                        "error_log": "str?",
                        "failing_tests": "list[str]?",
                        "target_file": "str?",
                        "top_k": "int?",
                    },
                    read_only=True,
                    timeout_seconds=15,
                    max_output_chars=12000,
                ),
                handler=search_code,
            ),
            Tool(
                definition=ToolDefinition(
                    name="grep_code",
                    description="Search text lines by regex pattern inside a repo.",
                    input_schema={
                        "repo_root": "str",
                        "pattern": "str",
                        "max_results": "int?",
                        "extensions": "list[str]?",
                    },
                    read_only=True,
                    timeout_seconds=10,
                    max_output_chars=10000,
                ),
                handler=grep_code,
            ),
            Tool(
                definition=ToolDefinition(
                    name="git_diff",
                    description="Show current git diff for a repository.",
                    input_schema={"repo_root": "str", "max_chars": "int?"},
                    read_only=True,
                    timeout_seconds=10,
                    max_output_chars=12000,
                ),
                handler=git_diff,
            ),
            Tool(
                definition=ToolDefinition(
                    name="run_tests",
                    description="Run an allowlisted test command inside repo_root.",
                    input_schema={
                        "repo_root": "str",
                        "command": "str",
                        "timeout_seconds": "float?",
                        "max_output_chars": "int?",
                    },
                    read_only=True,
                    timeout_seconds=60,
                    max_output_chars=12000,
                    enabled_by_default=True,
                ),
                handler=run_tests,
            ),
        ]
    )


def list_files(arguments: dict[str, Any]) -> str:
    repo_root = resolve_repo_root(str(arguments["repo_root"]))
    max_results = int(arguments.get("max_results", 200))
    include_hidden = bool(arguments.get("include_hidden", False))
    extensions = _normalize_extensions(arguments.get("extensions"))
    files: list[str] = []

    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in IGNORED_DIRS and (include_hidden or not dirname.startswith("."))
        ]
        for filename in filenames:
            if not include_hidden and filename.startswith("."):
                continue
            path = Path(root) / filename
            if is_ignored_path(path, repo_root):
                continue
            if extensions and path.suffix.lower() not in extensions:
                continue
            files.append(path.relative_to(repo_root).as_posix())
            if len(files) >= max_results:
                break
        if len(files) >= max_results:
            break

    return json.dumps(
        {"repo_root": str(repo_root), "count": len(files), "files": files},
        indent=2,
        ensure_ascii=False,
    )


def read_file(arguments: dict[str, Any]) -> str:
    repo_root = str(arguments["repo_root"])
    relative_path = str(arguments["path"])
    path = resolve_repo_path(repo_root, relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")
    if path.stat().st_size > int(arguments.get("max_file_size", 512 * 1024)):
        raise ToolSafetyError(f"Refusing to read oversized file: {relative_path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    start_line = max(int(arguments.get("start_line", 1)), 1)
    end_line = int(arguments.get("end_line", len(lines)))
    end_line = min(max(end_line, start_line), len(lines))
    content = "\n".join(lines[start_line - 1 : end_line])
    max_chars = int(arguments.get("max_chars", 12000))
    content, truncated = truncate_text(content, max_chars)
    return json.dumps(
        {
            "file": Path(relative_path).as_posix(),
            "start_line": start_line,
            "end_line": end_line,
            "truncated": truncated,
            "content": content,
        },
        indent=2,
        ensure_ascii=False,
    )


def search_code(arguments: dict[str, Any]) -> str:
    repo_root = str(arguments["repo_root"])
    resolve_repo_root(repo_root)
    results = HybridRetriever().retrieve(
        user_request=str(arguments.get("query", "")),
        error_log=str(arguments.get("error_log", "")),
        failing_tests=arguments.get("failing_tests", []),
        target_file=str(arguments.get("target_file", "")),
        repo_root=repo_root,
        top_k=int(arguments.get("top_k", 5)),
    )
    payload = []
    for result in results:
        snippet, truncated = truncate_text(result.chunk.content, 1200)
        payload.append(
            {
                "file_path": result.chunk.file_path,
                "start_line": result.chunk.start_line,
                "end_line": result.chunk.end_line,
                "symbol_name": result.chunk.symbol_name,
                "score": round(result.score, 4),
                "reason": result.reason,
                "truncated": truncated,
                "content": snippet,
            }
        )
    return json.dumps({"count": len(payload), "results": payload}, indent=2, ensure_ascii=False)


def grep_code(arguments: dict[str, Any]) -> str:
    repo_root = resolve_repo_root(str(arguments["repo_root"]))
    pattern = str(arguments["pattern"])
    max_results = int(arguments.get("max_results", 50))
    extensions = _normalize_extensions(arguments.get("extensions"))
    regex = re.compile(pattern)
    matches: list[dict[str, Any]] = []

    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in IGNORED_DIRS]
        for filename in filenames:
            path = Path(root) / filename
            if is_ignored_path(path, repo_root):
                continue
            if extensions and path.suffix.lower() not in extensions:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "file_path": path.relative_to(repo_root).as_posix(),
                            "line_number": line_number,
                            "line": line[:500],
                        }
                    )
                    if len(matches) >= max_results:
                        return json.dumps(
                            {"count": len(matches), "matches": matches},
                            indent=2,
                            ensure_ascii=False,
                        )

    return json.dumps({"count": len(matches), "matches": matches}, indent=2, ensure_ascii=False)


def git_diff(arguments: dict[str, Any]) -> str:
    repo_root = resolve_repo_root(str(arguments["repo_root"]))
    if not (repo_root / ".git").exists():
        raise ToolSafetyError("repo_root is not a git repository")
    max_chars = int(arguments.get("max_chars", 12000))
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={repo_root}", "diff", "--"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    )
    output, truncated = truncate_text(completed.stdout or completed.stderr, max_chars)
    return json.dumps(
        {
            "exit_code": completed.returncode,
            "truncated": truncated,
            "diff": output,
        },
        indent=2,
        ensure_ascii=False,
    )


def run_tests(arguments: dict[str, Any]) -> str:
    repo_root = resolve_repo_root(str(arguments["repo_root"]))
    command = str(arguments["command"])
    timeout_seconds = min(float(arguments.get("timeout_seconds", 60)), 60.0)
    max_output_chars = int(arguments.get("max_output_chars", 12000))
    argv = _normalize_test_command(command)
    if argv is None:
        raise ToolSafetyError(f"Test command is not allowlisted: {command}")

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        stdout, stdout_truncated = truncate_text(completed.stdout, max_output_chars)
        stderr, stderr_truncated = truncate_text(completed.stderr, max_output_chars)
        return json.dumps(
            {
                "command": " ".join(argv),
                "exit_code": completed.returncode,
                "duration_ms": duration_ms,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
            },
            indent=2,
            ensure_ascii=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        stdout, stdout_truncated = truncate_text(exc.stdout or "", max_output_chars)
        stderr, stderr_truncated = truncate_text(exc.stderr or "", max_output_chars)
        return json.dumps(
            {
                "command": " ".join(argv),
                "exit_code": None,
                "duration_ms": duration_ms,
                "timed_out": True,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
            },
            indent=2,
            ensure_ascii=False,
        )


def _normalize_extensions(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        value = [value]
    return {ext if str(ext).startswith(".") else f".{ext}" for ext in map(str, value)}


def _normalize_test_command(command: str) -> list[str] | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts:
        return None

    first = Path(parts[0]).name.lower()
    if first in {"pytest", "pytest.exe"}:
        return [sys.executable, "-m", "pytest", *parts[1:]]
    if first in {"unittest", "unittest.exe"}:
        return [sys.executable, "-m", "unittest", *parts[1:]]
    if first in {"python", "python.exe", "python3", "python3.exe"} and len(parts) >= 3:
        module = parts[2]
        if parts[1] == "-m" and module in {"pytest", "unittest"}:
            return [sys.executable, "-m", module, *parts[3:]]
    if first in {"mvn", "mvn.cmd"} and parts[1:] == ["test"]:
        return parts
    if first in {"gradle", "gradle.bat"} and parts[1:] == ["test"]:
        return parts
    if first in {"npm", "npm.cmd"} and parts[1:] == ["test"]:
        return parts
    return None


def _main() -> None:  # pragma: no cover - demo entry.
    parser = argparse.ArgumentParser(description="Run builtin tool demos.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--list-files", action="store_true")
    parser.add_argument("--search-code", default="")
    parser.add_argument("--read-file", default="")
    args = parser.parse_args()

    registry = build_default_registry()
    outputs: list[dict[str, Any]] = []
    if args.list_files:
        outputs.append({"tool": "list_files", "output": list_files({"repo_root": args.repo_root})})
    if args.search_code:
        outputs.append(
            {
                "tool": "search_code",
                "output": search_code({"repo_root": args.repo_root, "query": args.search_code}),
            }
        )
    if args.read_file:
        outputs.append(
            {
                "tool": "read_file",
                "output": registry.get("read_file").handler(
                    {"repo_root": args.repo_root, "path": args.read_file, "max_chars": 4000}
                ),
            }
        )
    print(json.dumps(outputs, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    _main()

