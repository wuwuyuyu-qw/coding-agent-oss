"""Patch dry-run and apply implementation."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .diff_audit import DiffAuditor
from .models import PatchApplyResult, PatchPlan, PatchType, truncate_text
from .rollback import RollbackManager
from .safety import resolve_repo_path, resolve_repo_root
from .validator import PatchValidator


class PatchApplier:
    """Dry-run and apply patch plans safely."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def dry_run(self, plan: PatchPlan, *, repo_root: str) -> PatchApplyResult:
        started = time.perf_counter()
        validation = PatchValidator().validate(plan, repo_root=repo_root)
        if not validation.valid:
            return PatchApplyResult(
                success=False,
                patch_id=plan.patch_id,
                failed_files=plan.target_files,
                error="; ".join(validation.errors),
                duration_ms=_elapsed_ms(started),
                metadata={"validation": validation.to_dict(), "dry_run": True},
            )
        if plan.patch_type == PatchType.UNIFIED_DIFF:
            return self._dry_run_unified_diff(plan, repo_root=repo_root, started=started)
        return PatchApplyResult(
            success=True,
            patch_id=plan.patch_id,
            applied_files=[],
            duration_ms=_elapsed_ms(started),
            metadata={"validation": validation.to_dict(), "dry_run": True},
        )

    def apply(self, plan: PatchPlan, *, repo_root: str) -> PatchApplyResult:
        started = time.perf_counter()
        dry = self.dry_run(plan, repo_root=repo_root)
        if not dry.success:
            return dry

        auditor = DiffAuditor()
        before_hash = auditor.file_hashes(repo_root=repo_root, files=plan.target_files)
        diff_before = _git_diff(repo_root, self.timeout_seconds)
        rollback = RollbackManager()
        try:
            if plan.patch_type == PatchType.SEARCH_REPLACE:
                applied = self._apply_search_replace(plan, repo_root=repo_root, rollback=rollback)
            else:
                applied = self._apply_unified_diff(plan, repo_root=repo_root)
            after_hash = auditor.file_hashes(repo_root=repo_root, files=plan.target_files)
            diff_after = _git_diff(repo_root, self.timeout_seconds)
            return PatchApplyResult(
                success=True,
                patch_id=plan.patch_id,
                applied_files=applied,
                diff_before=diff_before,
                diff_after=diff_after,
                duration_ms=_elapsed_ms(started),
                metadata={"files_before_hash": before_hash, "files_after_hash": after_hash},
            )
        except Exception as exc:
            rollback_performed = False
            try:
                rollback.restore_all()
                rollback_performed = True
            except Exception as rollback_exc:  # pragma: no cover - rare filesystem failure.
                exc = RuntimeError(f"{exc}; rollback_failed:{rollback_exc}")
            return PatchApplyResult(
                success=False,
                patch_id=plan.patch_id,
                failed_files=plan.target_files,
                error=str(exc),
                diff_before=diff_before,
                diff_after=_git_diff(repo_root, self.timeout_seconds),
                rollback_performed=rollback_performed,
                duration_ms=_elapsed_ms(started),
                metadata={"files_before_hash": before_hash},
            )

    def _dry_run_unified_diff(self, plan: PatchPlan, *, repo_root: str, started: float) -> PatchApplyResult:
        root = resolve_repo_root(repo_root)
        if not (root / ".git").exists():
            return PatchApplyResult(
                success=False,
                patch_id=plan.patch_id,
                failed_files=plan.target_files,
                error="repo_root is not a git repository; cannot run git apply --check",
                duration_ms=_elapsed_ms(started),
                metadata={"dry_run": True},
            )
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "apply", "--check", "-"],
            cwd=root,
            input=plan.raw_output,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
        )
        output, truncated = truncate_text(completed.stderr or completed.stdout, 4000)
        return PatchApplyResult(
            success=completed.returncode == 0,
            patch_id=plan.patch_id,
            failed_files=[] if completed.returncode == 0 else plan.target_files,
            error="" if completed.returncode == 0 else output,
            duration_ms=_elapsed_ms(started),
            metadata={"dry_run": True, "git_apply_check": True, "truncated": truncated},
        )

    def _apply_search_replace(self, plan: PatchPlan, *, repo_root: str, rollback: RollbackManager) -> list[str]:
        applied: list[str] = []
        for edit in plan.edits:
            path = resolve_repo_path(repo_root, edit.file_path)
            rollback.snapshot(path)
            content = path.read_text(encoding="utf-8")
            count = content.count(edit.search)
            if count == 0:
                raise RuntimeError(f"search_not_found:{edit.file_path}")
            if count > 1:
                raise RuntimeError(f"search_not_unique:{edit.file_path}")
            path.write_text(content.replace(edit.search, edit.replace, 1), encoding="utf-8")
            if edit.file_path not in applied:
                applied.append(edit.file_path)
        return applied

    def _apply_unified_diff(self, plan: PatchPlan, *, repo_root: str) -> list[str]:
        root = resolve_repo_root(repo_root)
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "apply", "-"],
            cwd=root,
            input=plan.raw_output,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
        )
        if completed.returncode != 0:
            output, _ = truncate_text(completed.stderr or completed.stdout, 4000)
            raise RuntimeError(output)
        return plan.target_files


def _git_diff(repo_root: str, timeout: float) -> str:
    root = resolve_repo_root(repo_root)
    if not (root / ".git").exists():
        return ""
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "diff", "--"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    output, _ = truncate_text(completed.stdout or completed.stderr, 12000)
    return output


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)

