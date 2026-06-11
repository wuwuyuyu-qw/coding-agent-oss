"""Load and validate benchmark task definitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BenchmarkTask

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


class BenchmarkTaskError(ValueError):
    """Raised when a benchmark task definition is invalid."""


class BenchmarkTaskLoader:
    def load_tasks(
        self,
        tasks_dir: str | Path,
        *,
        tags: list[str] | None = None,
        difficulty: str | None = None,
        max_tasks: int | None = None,
    ) -> list[BenchmarkTask]:
        root = Path(tasks_dir).resolve()
        if not root.exists() or not root.is_dir():
            raise BenchmarkTaskError(f"tasks_dir_not_found:{tasks_dir}")

        seen: set[str] = set()
        tasks: list[BenchmarkTask] = []
        for task_file in sorted(root.rglob("task.json")):
            task = self._load_task_file(task_file, root)
            if task.task_id in seen:
                raise BenchmarkTaskError(f"duplicate_task_id:{task.task_id}")
            seen.add(task.task_id)
            tasks.append(task)

        filtered = [
            task
            for task in tasks
            if self._matches(task, tags=tags or [], difficulty=difficulty)
        ]
        if max_tasks is not None:
            filtered = filtered[:max_tasks]
        return filtered

    def _load_task_file(self, task_file: Path, tasks_dir: Path) -> BenchmarkTask:
        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BenchmarkTaskError(f"invalid_json:{task_file}:{exc}") from exc

        required = ["task_id", "title", "repo_root", "target_file", "user_request"]
        for field_name in required:
            if not data.get(field_name):
                raise BenchmarkTaskError(f"missing_required_field:{field_name}:{task_file}")

        difficulty = data.get("difficulty", "easy")
        if difficulty not in _VALID_DIFFICULTIES:
            raise BenchmarkTaskError(f"invalid_difficulty:{difficulty}:{task_file}")

        repo_root = _resolve_inside(tasks_dir, data["repo_root"], "repo_root")
        target_file = str(data["target_file"])
        target_path = _resolve_inside(repo_root, target_file, "target_file")
        if target_path.is_dir():
            raise BenchmarkTaskError(f"target_file_is_directory:{target_file}")

        patch_output = str(data.get("patch_output") or "")
        patch_file = data.get("patch_file")
        if patch_file:
            patch_path = _resolve_inside(task_file.parent, str(patch_file), "patch_file")
            patch_output = patch_path.read_text(encoding="utf-8")
        elif not patch_output:
            default_patch = task_file.parent / "expected" / "patch.txt"
            if default_patch.exists():
                patch_output = default_patch.read_text(encoding="utf-8")

        return BenchmarkTask(
            task_id=str(data["task_id"]),
            title=str(data["title"]),
            repo_root=repo_root,
            target_file=target_file,
            user_request=str(data["user_request"]),
            error_log=str(data.get("error_log") or ""),
            failing_tests=_as_str_list(data.get("failing_tests", []), "failing_tests", task_file),
            test_command=data.get("test_command"),
            max_iterations=int(data.get("max_iterations", 3)),
            expected_success=bool(data.get("expected_success", True)),
            tags=_as_str_list(data.get("tags", []), "tags", task_file),
            difficulty=difficulty,  # type: ignore[arg-type]
            patch_output=patch_output,
            task_file=task_file,
        )

    @staticmethod
    def _matches(task: BenchmarkTask, *, tags: list[str], difficulty: str | None) -> bool:
        if difficulty and task.difficulty != difficulty:
            return False
        if tags and not set(tags).issubset(set(task.tags)):
            return False
        return True


def _resolve_inside(root: Path, value: str, field_name: str) -> Path:
    if Path(value).is_absolute():
        raise BenchmarkTaskError(f"{field_name}_must_be_relative:{value}")
    resolved = (root / value).resolve()
    if resolved != root and root not in resolved.parents:
        raise BenchmarkTaskError(f"{field_name}_path_traversal:{value}")
    return resolved


def _as_str_list(value: Any, field_name: str, task_file: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BenchmarkTaskError(f"invalid_list_field:{field_name}:{task_file}")
    return value
