from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.task_loader import BenchmarkTaskError, BenchmarkTaskLoader


def test_loads_valid_task(tmp_path: Path) -> None:
    task_file = _write_task(tmp_path, "toy_one")

    tasks = BenchmarkTaskLoader().load_tasks(tmp_path)

    assert len(tasks) == 1
    assert tasks[0].task_id == "toy_one"
    assert tasks[0].task_file == task_file


def test_missing_task_id_raises(tmp_path: Path) -> None:
    _write_task(tmp_path, "bad", overrides={"task_id": ""})

    with pytest.raises(BenchmarkTaskError, match="missing_required_field:task_id"):
        BenchmarkTaskLoader().load_tasks(tmp_path)


def test_repo_root_path_traversal_is_rejected(tmp_path: Path) -> None:
    _write_task(tmp_path, "bad", overrides={"repo_root": "../outside"})

    with pytest.raises(BenchmarkTaskError, match="repo_root_path_traversal"):
        BenchmarkTaskLoader().load_tasks(tmp_path)


def test_target_file_path_traversal_is_rejected(tmp_path: Path) -> None:
    _write_task(tmp_path, "bad", overrides={"target_file": "../secret.py"})

    with pytest.raises(BenchmarkTaskError, match="target_file_path_traversal"):
        BenchmarkTaskLoader().load_tasks(tmp_path)


def test_filters_by_tags_and_difficulty(tmp_path: Path) -> None:
    _write_task(tmp_path, "easy_python", overrides={"tags": ["python"], "difficulty": "easy"})
    _write_task(tmp_path, "hard_java", overrides={"tags": ["java"], "difficulty": "hard"})

    tasks = BenchmarkTaskLoader().load_tasks(tmp_path, tags=["python"], difficulty="easy")

    assert [task.task_id for task in tasks] == ["easy_python"]


def _write_task(root: Path, task_id: str, overrides: dict | None = None) -> Path:
    task_dir = root / task_id
    repo = task_dir / "repo"
    repo.mkdir(parents=True)
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    data = {
        "task_id": task_id,
        "title": "Toy task",
        "repo_root": f"{task_id}/repo",
        "target_file": "app.py",
        "user_request": "Fix app.",
        "tags": ["python"],
        "difficulty": "easy",
    }
    data.update(overrides or {})
    task_file = task_dir / "task.json"
    task_file.write_text(json.dumps(data), encoding="utf-8")
    return task_file
