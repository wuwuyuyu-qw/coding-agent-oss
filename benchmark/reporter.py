"""Generate JSON, Markdown, and CSV benchmark reports."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .models import BenchmarkRunReport, BenchmarkTaskResult, ReportFormat, truncate_text

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\[^\s]*"),
]


class BenchmarkReporter:
    def write_reports(
        self,
        report: BenchmarkRunReport,
        *,
        output_dir: str | Path,
        formats: list[ReportFormat],
    ) -> dict[str, str]:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}
        if "json" in formats:
            path = root / f"{report.run_id}.json"
            path.write_text(self.to_json(report), encoding="utf-8")
            paths["json"] = str(path)
        if "markdown" in formats:
            path = root / f"{report.run_id}.md"
            path.write_text(self.to_markdown(report), encoding="utf-8")
            paths["markdown"] = str(path)
        if "csv" in formats:
            path = root / f"{report.run_id}.csv"
            self.write_csv(report.task_results, path)
            paths["csv"] = str(path)
        return paths

    def to_json(self, report: BenchmarkRunReport) -> str:
        return _sanitize(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    def to_markdown(self, report: BenchmarkRunReport) -> str:
        metrics = report.summary_metrics
        lines = [
            "# Coding Agent Benchmark Report",
            "",
            "## Run Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Run ID | `{report.run_id}` |",
            f"| Created At | {report.created_at} |",
            f"| Mode | {report.mode} |",
            f"| Git Commit | `{report.git_commit or 'unavailable'}` |",
            f"| Total Tasks | {metrics.get('total_tasks', 0)} |",
            f"| Success Rate | {metrics.get('success_rate', 0.0)} |",
            "",
            "## Success Metrics",
            "",
            f"- Success count: {metrics.get('success_count', 0)}",
            f"- First-pass success rate: {metrics.get('first_pass_success_rate', 0.0)}",
            f"- Multi-turn success rate: {metrics.get('multi_turn_success_rate', 0.0)}",
            "",
            "## Failure Breakdown",
            "",
        ]
        for name, count in metrics.get("failure_breakdown", {}).items():
            lines.append(f"- {name}: {count}")
        lines.extend(
            [
                "",
                "## Patch Metrics",
                "",
                f"- Patch success rate: {metrics.get('patch_success_rate', 0.0)}",
                f"- Patch validation failure rate: {metrics.get('patch_validation_failure_rate', 0.0)}",
                f"- Patch apply failure rate: {metrics.get('patch_apply_failure_rate', 0.0)}",
                f"- Average changed files: {metrics.get('avg_changed_files', 0.0)}",
                f"- Average added lines: {metrics.get('avg_added_lines', 0.0)}",
                f"- Average deleted lines: {metrics.get('avg_deleted_lines', 0.0)}",
                f"- Suspicious change count: {metrics.get('suspicious_change_count', 0)}",
                "",
                "## RAG Metrics",
                "",
                f"- Average RAG chunk count: {metrics.get('avg_rag_chunk_count', 0.0)}",
                "",
                "## Tool Calling Metrics",
                "",
                f"- Average tool call count: {metrics.get('avg_tool_call_count', 0.0)}",
                "",
                "## Slowest Tasks",
                "",
            ]
        )
        for result in sorted(report.task_results, key=lambda item: item.duration_ms, reverse=True)[:5]:
            lines.append(f"- `{result.task_id}`: {result.duration_ms} ms")
        lines.extend(["", "## Failed Tasks", ""])
        failed = [result for result in report.task_results if not result.success]
        if not failed:
            lines.append("- None")
        for result in failed:
            reason = truncate_text(result.failure_reason or "", 300) or ""
            lines.append(f"- `{result.task_id}`: {result.failure_type} - {reason}")
        lines.extend(["", "## Per-task Results", "", "| Task | Success | Duration ms | Patch | RAG chunks | Tool calls |", "|---|---:|---:|---:|---:|---:|"])
        for result in report.task_results:
            lines.append(
                f"| `{result.task_id}` | {result.success} | {result.duration_ms} | "
                f"{result.patch_success} | {result.rag_chunk_count} | {result.tool_call_count} |"
            )
        lines.extend(
            [
                "",
                "## Limitations",
                "",
                "- Mock mode measures deterministic plumbing, not real model capability.",
                "- Toy tasks are intentionally small and should be expanded before drawing product conclusions.",
                "- Cost metrics are unavailable unless the Agent state exposes token usage.",
                "",
            ]
        )
        return _sanitize("\n".join(lines))

    def write_csv(self, results: list[BenchmarkTaskResult], path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "task_id",
                    "title",
                    "success",
                    "failure_type",
                    "duration_ms",
                    "retry_count",
                    "patch_success",
                    "tool_call_count",
                    "rag_chunk_count",
                    "changed_files",
                    "added_lines",
                    "deleted_lines",
                ],
            )
            writer.writeheader()
            for result in results:
                writer.writerow(
                    {
                        "task_id": result.task_id,
                        "title": result.title,
                        "success": result.success,
                        "failure_type": result.failure_type or "",
                        "duration_ms": result.duration_ms,
                        "retry_count": result.retry_count,
                        "patch_success": result.patch_success,
                        "tool_call_count": result.tool_call_count,
                        "rag_chunk_count": result.rag_chunk_count,
                        "changed_files": result.diff_changed_files,
                        "added_lines": result.diff_added_lines,
                        "deleted_lines": result.diff_deleted_lines,
                    }
                )


def _sanitize(text: str) -> str:
    text = truncate_text(text, 200_000) or ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text
