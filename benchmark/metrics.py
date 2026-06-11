"""Summary metrics for benchmark task results."""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from .models import BenchmarkTaskResult

_FAILURE_TYPES = [
    "llm_output_parse_failure",
    "patch_validation_failure",
    "patch_apply_failure",
    "test_failure",
    "timeout_failure",
    "tool_failure",
    "rag_failure",
    "unknown_failure",
]


class MetricCollector:
    def collect(self, results: list[BenchmarkTaskResult]) -> dict[str, Any]:
        total = len(results)
        if total == 0:
            return self._empty()

        success_count = sum(1 for result in results if result.success)
        first_pass_success_count = sum(
            1 for result in results if result.success and result.retry_count <= 1
        )
        multi_turn_success_count = sum(
            1 for result in results if result.success and result.retry_count > 1
        )
        failure_counts = Counter(result.failure_type or "none" for result in results if not result.success)

        durations = sorted(result.duration_ms for result in results)
        patch_validation_failures = failure_counts["patch_validation_failure"]
        patch_apply_failures = failure_counts["patch_apply_failure"]

        return {
            "total_tasks": total,
            "success_count": success_count,
            "success_rate": _rate(success_count, total),
            "first_pass_success_count": first_pass_success_count,
            "first_pass_success_rate": _rate(first_pass_success_count, total),
            "multi_turn_success_count": multi_turn_success_count,
            "multi_turn_success_rate": _rate(multi_turn_success_count, total),
            "failure_breakdown": {
                name: failure_counts.get(name, 0) for name in _FAILURE_TYPES
            },
            "avg_duration_ms": round(mean(durations), 2),
            "p50_duration_ms": _percentile(durations, 50),
            "p95_duration_ms": _percentile(durations, 95),
            "avg_retry_count": _avg(results, "retry_count"),
            "avg_tool_call_count": _avg(results, "tool_call_count"),
            "avg_rag_chunk_count": _avg(results, "rag_chunk_count"),
            "patch_success_rate": _rate(sum(1 for result in results if result.patch_success), total),
            "patch_validation_failure_rate": _rate(patch_validation_failures, total),
            "patch_apply_failure_rate": _rate(patch_apply_failures, total),
            "avg_changed_files": _avg(results, "diff_changed_files"),
            "avg_added_lines": _avg(results, "diff_added_lines"),
            "avg_deleted_lines": _avg(results, "diff_deleted_lines"),
            "suspicious_change_count": sum(result.suspicious_change_count for result in results),
            "total_prompt_tokens": None,
            "total_completion_tokens": None,
            "total_cost_estimate": None,
        }

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "total_tasks": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "first_pass_success_count": 0,
            "first_pass_success_rate": 0.0,
            "multi_turn_success_count": 0,
            "multi_turn_success_rate": 0.0,
            "failure_breakdown": {name: 0 for name in _FAILURE_TYPES},
            "avg_duration_ms": 0.0,
            "p50_duration_ms": 0.0,
            "p95_duration_ms": 0.0,
            "avg_retry_count": 0.0,
            "avg_tool_call_count": 0.0,
            "avg_rag_chunk_count": 0.0,
            "patch_success_rate": 0.0,
            "patch_validation_failure_rate": 0.0,
            "patch_apply_failure_rate": 0.0,
            "avg_changed_files": 0.0,
            "avg_added_lines": 0.0,
            "avg_deleted_lines": 0.0,
            "suspicious_change_count": 0,
            "total_prompt_tokens": None,
            "total_completion_tokens": None,
            "total_cost_estimate": None,
        }


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _avg(results: list[BenchmarkTaskResult], field_name: str) -> float:
    if not results:
        return 0.0
    return round(mean(float(getattr(result, field_name)) for result in results), 2)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    index = round((len(values) - 1) * percentile / 100)
    return round(values[index], 2)
