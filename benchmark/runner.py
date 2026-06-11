"""Benchmark runner orchestration."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import MetricCollector
from .mock_agent import MockAgent
from .models import BenchmarkConfig, BenchmarkRunReport, BenchmarkTask, BenchmarkTaskResult
from .reporter import BenchmarkReporter
from .task_loader import BenchmarkTaskLoader


class BenchmarkRunner:
    def __init__(self, config: BenchmarkConfig) -> None:
        self.config = config

    def run(self) -> tuple[BenchmarkRunReport, dict[str, str]]:
        tasks = BenchmarkTaskLoader().load_tasks(
            self.config.tasks_dir,
            tags=self.config.tags,
            difficulty=self.config.difficulty,
            max_tasks=self.config.max_tasks,
        )
        results = [self._run_one(task) for task in tasks]
        report = BenchmarkRunReport(
            run_id=f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            git_commit=_git_commit(),
            mode=self.config.mode,
            config=self.config.to_dict(),
            summary_metrics=MetricCollector().collect(results),
            task_results=results,
            environment_summary={
                "python": "available",
                "platform": os.name,
                "real_mode_configured": bool(os.environ.get("OPENAI_API_KEY")),
            },
        )
        paths = BenchmarkReporter().write_reports(
            report,
            output_dir=self.config.output_dir,
            formats=self.config.report_formats,
        )
        return report, paths

    def _run_one(self, task: BenchmarkTask) -> BenchmarkTaskResult:
        start = datetime.now(timezone.utc)
        started = time.perf_counter()
        try:
            payload = self._execute_task(task)
        except Exception as exc:
            payload = {
                "success": False,
                "failure_type": "unknown_failure",
                "failure_reason": f"{type(exc).__name__}: {exc}",
                "retry_count": 0,
            }
        end = datetime.now(timezone.utc)
        return self._to_result(task, payload, start, end, round((time.perf_counter() - started) * 1000, 2))

    def _execute_task(self, task: BenchmarkTask) -> dict[str, Any]:
        if self.config.mode == "mock":
            return MockAgent(
                timeout_seconds=self.config.timeout_seconds,
                enable_test_tool=self.config.enable_test_tool,
            ).run_task(task)
        if not os.environ.get("OPENAI_API_KEY"):
            return {
                "success": False,
                "failure_type": "unknown_failure",
                "failure_reason": "real mode requires OPENAI_API_KEY; benchmark skipped this task",
                "retry_count": 0,
            }
        return {
            "success": False,
            "failure_type": "unknown_failure",
            "failure_reason": "real mode entry is present but full benchmark orchestration is intentionally out of PR4 scope",
            "retry_count": 0,
        }

    @staticmethod
    def _to_result(
        task: BenchmarkTask,
        payload: dict[str, Any],
        start: datetime,
        end: datetime,
        duration_ms: float,
    ) -> BenchmarkTaskResult:
        rag = payload.get("rag_metadata") or {}
        tool = payload.get("tool_trace") or {}
        audit = payload.get("patch_audit") or {}
        patch_apply = payload.get("patch_apply_result") or {}
        return BenchmarkTaskResult(
            task_id=task.task_id,
            title=task.title,
            success=bool(payload.get("success")),
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            duration_ms=duration_ms,
            failure_type=payload.get("failure_type"),
            failure_reason=payload.get("failure_reason"),
            retry_count=int(payload.get("retry_count", 0)),
            patch_success=bool(payload.get("patch_success", patch_apply.get("success", False))),
            patch_failure_reason=payload.get("patch_failure_reason"),
            rag_enabled=bool(rag.get("enabled", False)),
            rag_chunk_count=int(rag.get("retrieved_chunk_count", 0) or 0),
            tool_call_count=int(tool.get("executed_count", 0) or 0),
            tool_failed_count=int(tool.get("failed_count", 0) or 0),
            test_exit_code=payload.get("test_exit_code"),
            diff_changed_files=len(audit.get("changed_files", []) or []),
            diff_added_lines=int(audit.get("line_added", 0) or 0),
            diff_deleted_lines=int(audit.get("line_deleted", 0) or 0),
            suspicious_change_count=len(audit.get("suspicious_changes", []) or []),
            metadata={
                "difficulty": task.difficulty,
                "tags": task.tags,
                "patch_validation": payload.get("patch_validation", {}),
            },
        )


def _git_commit() -> str | None:
    try:
        cwd = Path.cwd().resolve()
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={cwd}", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
