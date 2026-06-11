from __future__ import annotations

import json
from pathlib import Path

from benchmark.models import BenchmarkConfig
from benchmark.runner import BenchmarkRunner


def test_mock_mode_runs_toy_task_and_writes_reports(tmp_path: Path) -> None:
    tasks_dir = _write_task(tmp_path)
    output_dir = tmp_path / "results"

    report, paths = BenchmarkRunner(
        BenchmarkConfig(
            tasks_dir=tasks_dir,
            output_dir=output_dir,
            mode="mock",
            report_formats=["json", "markdown", "csv"],
        )
    ).run()

    assert report.summary_metrics["total_tasks"] == 1
    assert report.task_results[0].success is True
    assert report.task_results[0].patch_success is True
    assert report.task_results[0].diff_changed_files == 1
    assert all(Path(path).exists() for path in paths.values())
    assert (tasks_dir / "toy" / "repo" / "app.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"


def test_single_task_failure_does_not_stop_benchmark(tmp_path: Path) -> None:
    tasks_dir = _write_task(tmp_path)
    bad_dir = tasks_dir / "bad"
    (bad_dir / "repo").mkdir(parents=True)
    (bad_dir / "repo" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (bad_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "bad",
                "title": "Bad",
                "repo_root": "bad/repo",
                "target_file": "app.py",
                "user_request": "Fix it.",
                "patch_output": "",
            }
        ),
        encoding="utf-8",
    )

    report, _ = BenchmarkRunner(
        BenchmarkConfig(tasks_dir=tasks_dir, output_dir=tmp_path / "results", mode="mock")
    ).run()

    assert report.summary_metrics["total_tasks"] == 2
    assert report.summary_metrics["success_count"] == 1
    assert any(result.failure_type == "llm_output_parse_failure" for result in report.task_results)


def _write_task(root: Path) -> Path:
    tasks_dir = root / "tasks"
    task_dir = tasks_dir / "toy"
    repo = task_dir / "repo"
    expected = task_dir / "expected"
    expected.mkdir(parents=True)
    repo.mkdir(parents=True)
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (expected / "patch.txt").write_text(
        "```search\ndef f():\n    return 1\n```\n```replace\ndef f():\n    return 2\n```\n",
        encoding="utf-8",
    )
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "toy",
                "title": "Toy",
                "repo_root": "toy/repo",
                "target_file": "app.py",
                "user_request": "Return two.",
                "tags": ["python"],
                "difficulty": "easy",
            }
        ),
        encoding="utf-8",
    )
    return tasks_dir
