"""Deterministic mock execution path for benchmark tasks."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from patching.applier import PatchApplier
from patching.diff_audit import DiffAuditor
from patching.parser import PatchParser
from patching.validator import PatchValidator
from rag.hybrid_retriever import HybridRetriever
from tools.builtin_tools import build_default_registry
from tools.executor import ToolExecutor
from tools.models import ToolCall

from .models import BenchmarkTask, truncate_text


class MockAgent:
    """Run a preset patch through the real RAG/tool/patch/reportable path."""

    def __init__(self, *, timeout_seconds: float = 60.0, enable_test_tool: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self.enable_test_tool = enable_test_tool

    def run_task(self, task: BenchmarkTask) -> dict[str, Any]:
        if not task.patch_output.strip():
            return {
                "success": False,
                "failure_type": "llm_output_parse_failure",
                "failure_reason": "mock task has no patch_output or expected/patch.txt",
                "retry_count": 0,
            }

        with tempfile.TemporaryDirectory(prefix="coding-agent-benchmark-") as tmp:
            work_repo = Path(tmp) / "repo"
            shutil.copytree(task.repo_root, work_repo)
            _init_git_repo(work_repo)

            rag_metadata = self._run_rag(task, work_repo)
            tool_trace = self._run_tools(task, work_repo, include_tests=False)
            patch_payload = self._run_patch_pipeline(task, work_repo)

            test_result = None
            if self.enable_test_tool and task.test_command:
                test_trace = self._run_tools(task, work_repo, include_tests=True)
                tool_trace["executed_count"] += test_trace["executed_count"]
                tool_trace["failed_count"] += test_trace["failed_count"]
                test_result = test_trace.get("test_result")

            success = bool(patch_payload.get("patch_success"))
            failure_type = patch_payload.get("failure_type")
            failure_reason = patch_payload.get("failure_reason")
            if success and test_result is not None:
                success = test_result.get("exit_code") == 0
                if not success:
                    failure_type = "test_failure"
                    failure_reason = truncate_text(test_result.get("stderr") or test_result.get("stdout") or "tests failed", 1000)

            return {
                "success": success,
                "failure_type": None if success else failure_type or "unknown_failure",
                "failure_reason": None if success else failure_reason,
                "retry_count": 1,
                "rag_metadata": rag_metadata,
                "tool_trace": tool_trace,
                "test_exit_code": None if test_result is None else test_result.get("exit_code"),
                **patch_payload,
            }

    def _run_rag(self, task: BenchmarkTask, repo_root: Path) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            retriever = HybridRetriever()
            results = retriever.retrieve(
                user_request=task.user_request,
                error_log=task.error_log,
                failing_tests=task.failing_tests,
                target_file=task.target_file,
                repo_root=str(repo_root),
                top_k=5,
            )
            return {
                "enabled": True,
                "indexed_file_count": retriever.indexed_file_count,
                "retrieved_chunk_count": len(results),
                "reason": "ok",
                "elapsed_ms": _elapsed_ms(started),
            }
        except Exception as exc:
            return {
                "enabled": False,
                "indexed_file_count": 0,
                "retrieved_chunk_count": 0,
                "reason": f"rag_failed:{type(exc).__name__}",
                "elapsed_ms": _elapsed_ms(started),
            }

    def _run_tools(self, task: BenchmarkTask, repo_root: Path, *, include_tests: bool) -> dict[str, Any]:
        calls = [
            ToolCall(name="list_files", arguments={"repo_root": str(repo_root), "max_results": 100}),
            ToolCall(
                name="search_code",
                arguments={
                    "repo_root": str(repo_root),
                    "query": task.user_request,
                    "error_log": task.error_log,
                    "failing_tests": task.failing_tests,
                    "target_file": task.target_file,
                    "top_k": 5,
                },
            ),
            ToolCall(
                name="read_file",
                arguments={"repo_root": str(repo_root), "path": task.target_file, "max_chars": 8000},
            ),
            ToolCall(name="git_diff", arguments={"repo_root": str(repo_root), "max_chars": 8000}),
        ]
        if include_tests and task.test_command:
            calls.append(
                ToolCall(
                    name="run_tests",
                    arguments={
                        "repo_root": str(repo_root),
                        "command": task.test_command,
                        "timeout_seconds": min(self.timeout_seconds, 60.0),
                        "max_output_chars": 8000,
                    },
                )
            )

        trace = ToolExecutor(build_default_registry()).execute_many(calls)
        payload: dict[str, Any] = {
            "executed_count": len(calls),
            "failed_count": trace.failed_count,
            "success_count": trace.success_count,
        }
        if include_tests and trace.results:
            last = trace.results[-1]
            if last.tool_name == "run_tests" and last.success:
                try:
                    payload["test_result"] = json.loads(last.output)
                except json.JSONDecodeError:
                    payload["test_result"] = {"exit_code": None, "stderr": "invalid run_tests output"}
            elif last.tool_name == "run_tests":
                payload["test_result"] = {"exit_code": None, "stderr": last.error}
        return payload

    def _run_patch_pipeline(self, task: BenchmarkTask, repo_root: Path) -> dict[str, Any]:
        try:
            plan = PatchParser().parse(
                task.patch_output,
                repo_root=str(repo_root),
                default_target_file=task.target_file,
            )
        except Exception as exc:
            return _failure("llm_output_parse_failure", str(exc))

        validation = PatchValidator().validate(plan, repo_root=str(repo_root))
        if not validation.valid:
            return {
                **_failure("patch_validation_failure", "; ".join(validation.errors)),
                "patch_validation": validation.to_dict(),
            }

        applier = PatchApplier(timeout_seconds=min(self.timeout_seconds, 30.0))
        dry_run = applier.dry_run(plan, repo_root=str(repo_root))
        if not dry_run.success:
            return _failure("patch_apply_failure", dry_run.error or "dry-run failed")

        apply_result = applier.apply(plan, repo_root=str(repo_root))
        audit = DiffAuditor().audit(
            repo_root=str(repo_root),
            patch_id=plan.patch_id,
            files_before_hash=apply_result.metadata.get("files_before_hash", {}),
            files_after_hash=apply_result.metadata.get("files_after_hash", {}),
        )
        return {
            "patch_success": apply_result.success,
            "patch_failure_reason": None if apply_result.success else apply_result.error,
            "failure_type": None if apply_result.success else "patch_apply_failure",
            "failure_reason": None if apply_result.success else apply_result.error,
            "patch_plan": plan.to_dict(),
            "patch_validation": validation.to_dict(),
            "patch_apply_result": apply_result.to_dict(),
            "patch_audit": audit.to_dict(),
        }


def _failure(failure_type: str, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "patch_success": False,
        "patch_failure_reason": reason,
        "failure_type": failure_type,
        "failure_reason": truncate_text(reason, 1000),
    }


def _init_git_repo(repo_root: Path) -> None:
    if (repo_root / ".git").exists():
        return
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "benchmark@example.invalid")
    _git(repo_root, "config", "user.name", "Benchmark")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "benchmark baseline")


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        shell=False,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
