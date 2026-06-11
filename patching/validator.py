"""Patch plan validation."""

from __future__ import annotations

from pathlib import Path

from .errors import PatchValidationError
from .models import PatchPlan, PatchType, PatchValidationResult
from .safety import resolve_repo_path, resolve_repo_root


class PatchValidator:
    """Validate patch plans without writing files."""

    def __init__(self, *, max_files: int = 20, max_patch_bytes: int = 200 * 1024) -> None:
        self.max_files = max_files
        self.max_patch_bytes = max_patch_bytes

    def validate(self, plan: PatchPlan, *, repo_root: str) -> PatchValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        target_files = list(plan.target_files)

        try:
            resolve_repo_root(repo_root)
        except PatchValidationError as exc:
            errors.append(str(exc))

        if plan.patch_type not in {PatchType.SEARCH_REPLACE, PatchType.UNIFIED_DIFF}:
            errors.append("unsupported_patch_type")
        if not target_files:
            errors.append("target_files_empty")
        if len(target_files) > self.max_files:
            errors.append("too_many_files")
        if len(plan.raw_output.encode("utf-8")) > self.max_patch_bytes:
            errors.append("patch_too_large")

        for file_path in target_files:
            try:
                resolve_repo_path(repo_root, file_path)
            except PatchValidationError as exc:
                errors.append(str(exc))
            warnings.extend(_risk_warnings_for_path(file_path))

        if plan.patch_type == PatchType.SEARCH_REPLACE:
            for index, edit in enumerate(plan.edits, start=1):
                if not edit.search.strip():
                    errors.append(f"edit_{index}:search_empty")
                if edit.replace == "":
                    warnings.append(f"edit_{index}:replace_empty")
                try:
                    path = resolve_repo_path(repo_root, edit.file_path)
                    content = path.read_text(encoding="utf-8")
                    count = content.count(edit.search)
                    if count == 0:
                        errors.append(f"edit_{index}:search_not_found")
                    elif count > 1:
                        errors.append(f"edit_{index}:search_not_unique")
                except (OSError, UnicodeDecodeError, PatchValidationError) as exc:
                    errors.append(f"edit_{index}:{exc}")
        elif plan.patch_type == PatchType.UNIFIED_DIFF:
            if not plan.unified_diff or not plan.unified_diff.files_changed:
                errors.append("unified_diff_files_empty")
            if not plan.unified_diff or not plan.unified_diff.hunks:
                errors.append("unified_diff_hunks_empty")

        if _touches_only_tests(target_files):
            warnings.append("modifies_tests_without_source")

        risk_level = "high" if errors or len(warnings) >= 3 else "medium" if warnings else "low"
        return PatchValidationResult(
            valid=not errors,
            errors=errors,
            warnings=sorted(set(warnings)),
            target_files=target_files,
            edit_count=len(plan.edits) if plan.patch_type == PatchType.SEARCH_REPLACE else len(plan.unified_diff.hunks if plan.unified_diff else []),
            risk_level=risk_level,
        )


def _risk_warnings_for_path(file_path: str) -> list[str]:
    warnings: list[str] = []
    name = Path(file_path).name.lower()
    suffix = Path(file_path).suffix.lower()
    if name.startswith("test_") or name.endswith("_test.py") or "/tests/" in file_path.replace("\\", "/"):
        warnings.append("modifies_test_file")
    if name in {"requirements.txt", "pyproject.toml", "pom.xml", "package.json", "package-lock.json"}:
        warnings.append("modifies_dependency_or_build_file")
    if "auth" in name or "security" in name:
        warnings.append("modifies_security_related_file")
    if suffix in {".yml", ".yaml", ".json", ".toml", ".ini", ".cfg"}:
        warnings.append("modifies_config_file")
    return warnings


def _touches_only_tests(paths: list[str]) -> bool:
    if not paths:
        return False
    normalized = [path.replace("\\", "/") for path in paths]
    return all("/tests/" in path or Path(path).name.startswith("test_") for path in normalized)

