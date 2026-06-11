"""CLI for reproducible benchmark runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from .models import BenchmarkConfig
from .runner import BenchmarkRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Coding Agent benchmarks.")
    parser.add_argument("--tasks-dir", default="benchmark/tasks")
    parser.add_argument("--output-dir", default="benchmark/results")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default=None)
    parser.add_argument("--enable-test-tool", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--formats", nargs="+", choices=["json", "markdown", "csv"], default=["json", "markdown", "csv"])
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        tasks_dir=Path(args.tasks_dir),
        output_dir=Path(args.output_dir),
        mode=args.mode,
        max_tasks=args.max_tasks,
        tags=args.tags,
        difficulty=args.difficulty,
        enable_test_tool=args.enable_test_tool,
        timeout_seconds=args.timeout_seconds,
        report_formats=args.formats,
    )
    report, paths = BenchmarkRunner(config).run()
    print(f"Benchmark run: {report.run_id}")
    print(f"Mode: {report.mode}")
    print(f"Tasks: {report.summary_metrics.get('total_tasks', 0)}")
    print(f"Success rate: {report.summary_metrics.get('success_rate', 0.0)}")
    print("Reports:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
