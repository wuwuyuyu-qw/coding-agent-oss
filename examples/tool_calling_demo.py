"""Minimal demo for the internal Tool Calling Framework."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.builtin_tools import build_default_registry
from tools.context_builder import ToolContextBuilder
from tools.executor import ToolExecutor
from tools.models import ToolCall


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tool calling demo.")
    parser.add_argument("--repo-root", default=str(Path(__file__).parent / "buggy_project"))
    args = parser.parse_args()

    repo_root = str(Path(args.repo_root).resolve())
    calls = [
        ToolCall(name="list_files", arguments={"repo_root": repo_root, "max_results": 20}),
        ToolCall(
            name="search_code",
            arguments={
                "repo_root": repo_root,
                "query": "fix divide by zero",
                "target_file": "calculator.py",
                "top_k": 3,
            },
        ),
        ToolCall(
            name="read_file",
            arguments={"repo_root": repo_root, "path": "calculator.py", "max_chars": 2000},
        ),
    ]
    trace = ToolExecutor(build_default_registry()).execute_many(calls)
    context = ToolContextBuilder(max_context_chars=6000).build_context(trace=trace)
    print(context)


if __name__ == "__main__":
    main()
