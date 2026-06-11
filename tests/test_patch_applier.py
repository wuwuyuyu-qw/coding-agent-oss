from __future__ import annotations

import subprocess
from pathlib import Path

from patching.applier import PatchApplier
from patching.models import PatchPlan, PatchType, SearchReplaceEdit
from patching.parser import PatchParser


def _plan(file_path: str, search: str, replace: str) -> PatchPlan:
    return PatchPlan(
        patch_type=PatchType.SEARCH_REPLACE,
        edits=[SearchReplaceEdit(file_path=file_path, search=search, replace=replace)],
        raw_output="raw",
        target_files=[file_path],
    )


def test_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")

    result = PatchApplier().dry_run(_plan("app.py", "old\n", "new\n"), repo_root=str(tmp_path))

    assert result.success is True
    assert path.read_text(encoding="utf-8") == "old\n"


def test_search_replace_apply_modifies_file(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")

    result = PatchApplier().apply(_plan("app.py", "old\n", "new\n"), repo_root=str(tmp_path))

    assert result.success is True
    assert path.read_text(encoding="utf-8") == "new\n"
    assert result.applied_files == ["app.py"]


def test_search_replace_apply_failure_keeps_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")

    result = PatchApplier().apply(_plan("app.py", "missing", "new"), repo_root=str(tmp_path))

    assert result.success is False
    assert path.read_text(encoding="utf-8") == "old\n"


def test_apply_failure_after_partial_write_rolls_back(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("old-a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("old-b\n", encoding="utf-8")
    plan = PatchPlan(
        patch_type=PatchType.SEARCH_REPLACE,
        edits=[
            SearchReplaceEdit(file_path="a.py", search="old-a\n", replace="new-a\n"),
            SearchReplaceEdit(file_path="a.py", search="old-a\n", replace="never\n"),
            SearchReplaceEdit(file_path="b.py", search="old-b\n", replace="new-b\n"),
        ],
        raw_output="raw",
        target_files=["a.py", "b.py"],
    )

    result = PatchApplier().apply(plan, repo_root=str(tmp_path))

    assert result.success is False
    assert result.rollback_performed is True
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old-a\n"


def test_unified_diff_dry_run_uses_git_apply_check(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    plan = PatchParser().parse(
        """--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
"""
    )

    result = PatchApplier().dry_run(plan, repo_root=str(tmp_path))

    assert result.success is True
    assert result.metadata["git_apply_check"] is True


def test_unified_diff_non_git_repo_fails_clearly(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    plan = PatchParser().parse(
        """--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
"""
    )

    result = PatchApplier().dry_run(plan, repo_root=str(tmp_path))

    assert result.success is False
    assert "not a git repository" in result.error


def _init_git_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True, shell=False)
