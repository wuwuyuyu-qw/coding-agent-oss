# Benchmark + Report

This project now includes a small, reproducible benchmark harness for checking
the Coding Agent repair pipeline beyond one-off demos.

## Why Benchmark

Benchmarks make repair behavior measurable. They help track first-pass success,
multi-turn success, patch failures, tool usage, RAG retrieval, and diff audit
signals across a batch of tasks.

## Task Format

Each task is a `task.json` file under `benchmark/tasks/`:

```json
{
  "task_id": "toy_divide_by_zero",
  "title": "Fix divide by zero handling",
  "repo_root": "toy_divide_by_zero/repo",
  "target_file": "calculator.py",
  "user_request": "Fix divide by zero behavior.",
  "error_log": "ZeroDivisionError: division by zero",
  "failing_tests": ["tests/test_calculator.py::test_divide_by_zero"],
  "test_command": "pytest",
  "max_iterations": 3,
  "expected_success": true,
  "tags": ["python", "runtime-error", "single-file"],
  "difficulty": "easy"
}
```

`repo_root` is relative to the tasks directory, and `target_file` is relative to
`repo_root`. Path traversal and absolute paths are rejected.

## Mock Mode And Real Mode

Mock mode does not call a real LLM. It reads a preset patch from
`expected/patch.txt` or `patch_output`, then still runs the real PatchParser,
PatchValidator, PatchApplier, DiffAuditor, RAG retrieval, tool pass, and report
generation.

Real mode currently provides a guarded entry. If `OPENAI_API_KEY` is absent, it
returns a clear skipped/failure result instead of crashing. Full real-mode
benchmark orchestration is intentionally outside this PR.

## Running

```bash
python -m benchmark.cli \
  --tasks-dir benchmark/tasks \
  --output-dir benchmark/results \
  --mode mock \
  --formats json markdown csv
```

Add `--enable-test-tool` to run each task's allowlisted `test_command`.

## Report Fields

JSON reports include run metadata, git commit, config, summary metrics, task
results, and environment summary. Markdown reports are readable in docs or
README files. CSV reports contain one task per row for spreadsheet analysis.

## Metric Notes

- `first_pass_success`: task succeeded with one generated patch attempt.
- `multi_turn_success`: task succeeded after more than one attempt.
- `patch_validation_failure`: PatchValidator rejected the patch.
- `patch_apply_failure`: dry-run or apply failed.
- `tool_failure`: deterministic tool pass failures.
- `rag_chunk_count`: number of chunks retrieved for the task.
- `diff_changed_files`: number of files changed by the patch audit.

## Adding Tasks

Create a new directory under `benchmark/tasks/`, add a small `repo/`, a
`task.json`, and either `expected/patch.txt` or `patch_output` in the JSON. Keep
tasks self-contained and avoid external services.

## Current Limitations

- Mock mode does not represent real LLM capability.
- Toy task coverage is intentionally small.
- Real mode depends on API configuration.
- Cost metrics depend on future token usage fields in Agent state.

## Future Extensions

- SWE-bench style datasets.
- Multi-language task packs.
- Regression benchmark suites.
- Dashboard views.
- CI benchmark gates.
- Model comparison experiments.
