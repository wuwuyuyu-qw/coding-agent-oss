"""Data models for reproducible benchmark runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


BenchmarkMode = Literal["mock", "real"]
ReportFormat = Literal["json", "markdown", "csv"]
Difficulty = Literal["easy", "medium", "hard"]


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    title: str
    repo_root: Path
    target_file: str
    user_request: str
    error_log: str = ""
    failing_tests: list[str] = field(default_factory=list)
    test_command: str | None = None
    max_iterations: int = 3
    expected_success: bool = True
    tags: list[str] = field(default_factory=list)
    difficulty: Difficulty = "easy"
    patch_output: str = ""
    task_file: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["repo_root"] = str(self.repo_root)
        data["task_file"] = str(self.task_file) if self.task_file else None
        return data


@dataclass
class BenchmarkConfig:
    tasks_dir: Path
    output_dir: Path
    mode: BenchmarkMode = "mock"
    max_tasks: int | None = None
    tags: list[str] = field(default_factory=list)
    difficulty: Difficulty | None = None
    enable_test_tool: bool = False
    timeout_seconds: float = 60.0
    report_formats: list[ReportFormat] = field(default_factory=lambda: ["json", "markdown", "csv"])

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tasks_dir"] = str(self.tasks_dir)
        data["output_dir"] = str(self.output_dir)
        return data


@dataclass
class BenchmarkTaskResult:
    task_id: str
    title: str
    success: bool
    start_time: str
    end_time: str
    duration_ms: float
    failure_type: str | None = None
    failure_reason: str | None = None
    retry_count: int = 0
    patch_success: bool = False
    patch_failure_reason: str | None = None
    rag_enabled: bool = False
    rag_chunk_count: int = 0
    tool_call_count: int = 0
    tool_failed_count: int = 0
    test_exit_code: int | None = None
    diff_changed_files: int = 0
    diff_added_lines: int = 0
    diff_deleted_lines: int = 0
    suspicious_change_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkRunReport:
    run_id: str
    created_at: str
    git_commit: str | None
    mode: BenchmarkMode
    config: dict[str, Any]
    summary_metrics: dict[str, Any]
    task_results: list[BenchmarkTaskResult]
    environment_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "git_commit": self.git_commit,
            "mode": self.mode,
            "config": self.config,
            "summary_metrics": self.summary_metrics,
            "task_results": [result.to_dict() for result in self.task_results],
            "environment_summary": self.environment_summary,
        }


def truncate_text(text: str | None, max_chars: int = 4000) -> str | None:
    if text is None or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...<truncated>"
