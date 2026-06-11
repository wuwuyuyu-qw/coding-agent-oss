from __future__ import annotations

import subprocess
from pathlib import Path

from patching.diff_audit import DiffAuditor


def test_git_repo_generates_diff_summary(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    path.write_text("old\nnew\n", encoding="utf-8")

    audit = DiffAuditor().audit(repo_root=str(tmp_path), patch_id="p1")

    assert "app.py" in audit.changed_files
    assert audit.line_added >= 1
    assert "app.py" in audit.diff_summary


def test_non_git_repo_degrades_with_warning(tmp_path: Path) -> None:
    audit = DiffAuditor().audit(repo_root=str(tmp_path), patch_id="p1")

    assert audit.metadata["warning"] == "repo_root is not a git repository"


def test_large_diff_is_truncated(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    path.write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")

    audit = DiffAuditor(max_diff_chars=100).audit(repo_root=str(tmp_path), patch_id="p1")

    assert audit.metadata["truncated"] is True
    assert len(audit.metadata["diff"]) <= 100


def test_suspicious_changes_are_identified(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_app.py"
    test_file.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    test_file.unlink()

    audit = DiffAuditor().audit(repo_root=str(tmp_path), patch_id="p1")

    assert "deletes_tests" in audit.suspicious_changes


def _init_git_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True, shell=False)

