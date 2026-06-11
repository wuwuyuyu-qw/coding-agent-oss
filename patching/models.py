"""Patch Engine data models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PatchType(str, Enum):
    SEARCH_REPLACE = "search_replace"
    UNIFIED_DIFF = "unified_diff"


@dataclass(frozen=True)
class SearchReplaceEdit:
    file_path: str
    search: str
    replace: str
    occurrence_policy: str = "one"
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class UnifiedDiffHunk:
    file_path: str
    header: str
    old_start: int | None = None
    old_count: int | None = None
    new_start: int | None = None
    new_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class UnifiedDiffPatch:
    raw_diff: str
    files_changed: list[str]
    hunks: list[UnifiedDiffHunk]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_diff": self.raw_diff,
            "files_changed": self.files_changed,
            "hunks": [hunk.to_dict() for hunk in self.hunks],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PatchPlan:
    patch_type: PatchType
    raw_output: str
    target_files: list[str]
    edits: list[SearchReplaceEdit] = field(default_factory=list)
    unified_diff: UnifiedDiffPatch | None = None
    patch_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "patch_type": self.patch_type.value,
            "edits": [edit.to_dict() for edit in self.edits],
            "unified_diff": self.unified_diff.to_dict() if self.unified_diff else None,
            "raw_output": self.raw_output,
            "target_files": self.target_files,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PatchValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    edit_count: int = 0
    risk_level: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class PatchApplyResult:
    success: bool
    patch_id: str
    applied_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    error: str = ""
    diff_before: str = ""
    diff_after: str = ""
    rollback_performed: bool = False
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class PatchAudit:
    patch_id: str
    files_before_hash: dict[str, str] = field(default_factory=dict)
    files_after_hash: dict[str, str] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    diff_summary: str = ""
    line_added: int = 0
    line_deleted: int = 0
    suspicious_changes: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def truncate_text(text: str, max_chars: int = 12000) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    suffix = "\n...<truncated>"
    if max_chars <= len(suffix):
        return text[:max_chars], True
    return text[: max_chars - len(suffix)] + suffix, True

