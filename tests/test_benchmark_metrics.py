from __future__ import annotations

from benchmark.metrics import MetricCollector
from benchmark.models import BenchmarkTaskResult


def test_collects_success_and_duration_metrics() -> None:
    results = [
        _result("a", True, 100, retry_count=1, patch_success=True, rag_chunk_count=2),
        _result("b", True, 300, retry_count=2, patch_success=True, tool_call_count=3),
        _result("c", False, 200, failure_type="patch_apply_failure"),
    ]

    metrics = MetricCollector().collect(results)

    assert metrics["total_tasks"] == 3
    assert metrics["success_rate"] == 0.6667
    assert metrics["first_pass_success_rate"] == 0.3333
    assert metrics["multi_turn_success_rate"] == 0.3333
    assert metrics["failure_breakdown"]["patch_apply_failure"] == 1
    assert metrics["avg_duration_ms"] == 200
    assert metrics["p50_duration_ms"] == 200
    assert metrics["p95_duration_ms"] == 300
    assert metrics["avg_tool_call_count"] == 1
    assert metrics["avg_rag_chunk_count"] == 0.67


def test_empty_results_do_not_crash() -> None:
    metrics = MetricCollector().collect([])

    assert metrics["total_tasks"] == 0
    assert metrics["success_rate"] == 0.0
    assert metrics["failure_breakdown"]["unknown_failure"] == 0


def _result(
    task_id: str,
    success: bool,
    duration_ms: float,
    *,
    failure_type: str | None = None,
    retry_count: int = 0,
    patch_success: bool = False,
    tool_call_count: int = 0,
    rag_chunk_count: int = 0,
) -> BenchmarkTaskResult:
    return BenchmarkTaskResult(
        task_id=task_id,
        title=task_id,
        success=success,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:00:00Z",
        duration_ms=duration_ms,
        failure_type=failure_type,
        retry_count=retry_count,
        patch_success=patch_success,
        tool_call_count=tool_call_count,
        rag_chunk_count=rag_chunk_count,
    )
