from __future__ import annotations

from pathlib import Path

from benchmark.models import BenchmarkRunReport, BenchmarkTaskResult
from benchmark.reporter import BenchmarkReporter


def test_reporter_generates_json_markdown_and_csv(tmp_path: Path) -> None:
    report = _report()

    paths = BenchmarkReporter().write_reports(
        report,
        output_dir=tmp_path,
        formats=["json", "markdown", "csv"],
    )

    assert set(paths) == {"json", "markdown", "csv"}
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).read_text(encoding="utf-8").startswith("# Coding Agent Benchmark Report")
    assert "task_id,title,success" in Path(paths["csv"]).read_text(encoding="utf-8")


def test_reporter_redacts_secrets_and_personal_paths() -> None:
    report = _report(reason="OPENAI_API_KEY=sk-secret-value C:\\Users\\someone\\secret.txt")

    text = BenchmarkReporter().to_json(report)

    assert "sk-secret-value" not in text
    assert "C:\\Users\\someone" not in text
    assert "<redacted>" in text


def test_reporter_truncates_very_long_output() -> None:
    report = _report(reason="x" * 250_000)

    text = BenchmarkReporter().to_json(report)

    assert len(text) < 210_000
    assert "...<truncated>" in text


def _report(reason: str = "") -> BenchmarkRunReport:
    result = BenchmarkTaskResult(
        task_id="toy",
        title="Toy",
        success=not bool(reason),
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:00:01Z",
        duration_ms=1000,
        failure_type="unknown_failure" if reason else None,
        failure_reason=reason or None,
        patch_success=True,
    )
    return BenchmarkRunReport(
        run_id="run",
        created_at="2026-01-01T00:00:00Z",
        git_commit="abc",
        mode="mock",
        config={},
        summary_metrics={"total_tasks": 1, "success_rate": 1.0, "failure_breakdown": {}},
        task_results=[result],
        environment_summary={},
    )
