"""Git diff audit utilities for Patch Engine."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

from .models import PatchAudit, truncate_text
from .safety import resolve_repo_path, resolve_repo_root


class DiffAuditor:
    """Collect git diff metadata and suspicious-change signals."""

    def __init__(self, *, max_diff_chars: int = 12000, timeout_seconds: float = 10.0) -> None:
        self.max_diff_chars = max_diff_chars
        self.timeout_seconds = timeout_seconds

    def file_hashes(self, *, repo_root: str, files: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for file_path in files:
            path = resolve_repo_path(repo_root, file_path)
            if path.exists() and path.is_file():
                hashes[file_path] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                hashes[file_path] = ""
        return hashes

    def audit(
        self,
        *,
        repo_root: str,
        patch_id: str,
        files_before_hash: dict[str, str] | None = None,
        files_after_hash: dict[str, str] | None = None,
    ) -> PatchAudit:
        root = resolve_repo_root(repo_root)
        if not (root / ".git").exists():
            return PatchAudit(
                patch_id=patch_id,
                files_before_hash=files_before_hash or {},
                files_after_hash=files_after_hash or {},
                metadata={"warning": "repo_root is not a git repository"},
            )

        stat = _run_git(root, ["diff", "--stat"], self.timeout_seconds)
        diff = _run_git(root, ["diff", "--"], self.timeout_seconds)
        diff_text, truncated = truncate_text(diff.stdout or diff.stderr, self.max_diff_chars)
        changed_files = _changed_files_from_diff(diff.stdout)
        added, deleted = _count_added_deleted(diff.stdout)
        suspicious = _suspicious_changes(changed_files, added, deleted, diff.stdout)
        return PatchAudit(
            patch_id=patch_id,
            files_before_hash=files_before_hash or {},
            files_after_hash=files_after_hash or {},
            changed_files=changed_files,
            diff_summary=(stat.stdout or stat.stderr).strip(),
            line_added=added,
            line_deleted=deleted,
            suspicious_changes=suspicious,
            metadata={"diff": diff_text, "truncated": truncated},
            created_at=time.time(),
        )


def _run_git(root: Path, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={root}", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _changed_files_from_diff(diff: str) -> list[str]:
    files = re.findall(r"^\+\+\+ b/(.+)$", diff, flags=re.MULTILINE)
    files.extend(re.findall(r"^--- a/(.+)$", diff, flags=re.MULTILINE))
    return sorted({file for file in files if file != "/dev/null"})


def _count_added_deleted(diff: str) -> tuple[int, int]:
    added = 0
    deleted = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def _suspicious_changes(files: list[str], added: int, deleted: int, diff: str) -> list[str]:
    signals: list[str] = []
    if deleted > 200 or deleted > added * 5 + 20:
        signals.append("large_deletion")
    if any("/tests/" in file or Path(file).name.startswith("test_") for file in files) and "deleted file mode" in diff:
        signals.append("deletes_tests")
    if any(Path(file).name in {".env", "poetry.lock", "package-lock.json"} or file.endswith(".lock") for file in files):
        signals.append("touches_sensitive_or_lock_file")
    if len(files) > 20:
        signals.append("too_many_changed_files")
    return signals
