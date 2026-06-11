from __future__ import annotations

from pathlib import Path

from patching.models import PatchPlan, PatchType, SearchReplaceEdit
from patching.parser import PatchParser
from patching.validator import PatchValidator


def _plan(file_path: str, search: str, replace: str) -> PatchPlan:
    return PatchPlan(
        patch_type=PatchType.SEARCH_REPLACE,
        edits=[SearchReplaceEdit(file_path=file_path, search=search, replace=replace)],
        raw_output="raw",
        target_files=[file_path],
    )


def test_search_unique_hit_is_valid(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")

    result = PatchValidator().validate(_plan("app.py", "old\n", "new\n"), repo_root=str(tmp_path))

    assert result.valid is True


def test_search_zero_hit_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")

    result = PatchValidator().validate(_plan("app.py", "missing", "new"), repo_root=str(tmp_path))

    assert result.valid is False
    assert any("search_not_found" in error for error in result.errors)


def test_search_multiple_hits_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old old", encoding="utf-8")

    result = PatchValidator().validate(_plan("app.py", "old", "new"), repo_root=str(tmp_path))

    assert result.valid is False
    assert any("search_not_unique" in error for error in result.errors)


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    result = PatchValidator().validate(_plan("../x.py", "old", "new"), repo_root=str(tmp_path))

    assert result.valid is False
    assert "path_traversal" in result.errors


def test_sensitive_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=x", encoding="utf-8")

    result = PatchValidator().validate(_plan(".env", "SECRET", "X"), repo_root=str(tmp_path))

    assert result.valid is False
    assert any("sensitive_path" in error for error in result.errors)


def test_too_many_files_is_invalid(tmp_path: Path) -> None:
    files = []
    edits = []
    for index in range(3):
        name = f"f{index}.py"
        (tmp_path / name).write_text("old", encoding="utf-8")
        files.append(name)
        edits.append(SearchReplaceEdit(file_path=name, search="old", replace="new"))
    plan = PatchPlan(
        patch_type=PatchType.SEARCH_REPLACE,
        edits=edits,
        raw_output="raw",
        target_files=files,
    )

    result = PatchValidator(max_files=2).validate(plan, repo_root=str(tmp_path))

    assert result.valid is False
    assert "too_many_files" in result.errors


def test_high_risk_change_generates_warning(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    result = PatchValidator().validate(
        _plan("requirements.txt", "pytest\n", "pytest\nrequests\n"),
        repo_root=str(tmp_path),
    )

    assert result.valid is True
    assert "modifies_dependency_or_build_file" in result.warnings


def test_unified_diff_requires_hunks(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    plan = PatchParser().parse(
        """--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
"""
    )

    result = PatchValidator().validate(plan, repo_root=str(tmp_path))

    assert result.valid is True

