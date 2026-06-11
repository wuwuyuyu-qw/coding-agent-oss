"""Patch parser for Search/Replace and basic unified diff outputs."""

from __future__ import annotations

import re
from pathlib import Path

from core.patch_apply import parse_search_replace_pairs

from .errors import PatchParseError
from .models import PatchPlan, PatchType, SearchReplaceEdit, UnifiedDiffHunk, UnifiedDiffPatch

_HUNK_RE = re.compile(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


class PatchParser:
    """Parse LLM raw patch output into a PatchPlan."""

    def parse(
        self,
        raw_output: str,
        *,
        repo_root: str | None = None,
        default_target_file: str | None = None,
    ) -> PatchPlan:
        raw_output = raw_output or ""
        if _looks_like_unified_diff(raw_output):
            return self._parse_unified_diff(raw_output)
        return self._parse_search_replace(raw_output, default_target_file=default_target_file)

    def _parse_search_replace(self, raw_output: str, *, default_target_file: str | None) -> PatchPlan:
        if not default_target_file:
            raise PatchParseError("Search/Replace patch requires default_target_file")
        try:
            pairs = parse_search_replace_pairs(raw_output)
        except ValueError as exc:
            raise PatchParseError(str(exc)) from exc
        edits: list[SearchReplaceEdit] = []
        for search, replace in pairs:
            if not search.strip():
                raise PatchParseError("search_empty")
            if replace == "":
                raise PatchParseError("replace_empty")
            edits.append(
                SearchReplaceEdit(
                    file_path=default_target_file,
                    search=search,
                    replace=replace,
                )
            )
        return PatchPlan(
            patch_type=PatchType.SEARCH_REPLACE,
            raw_output=raw_output,
            edits=edits,
            target_files=sorted({edit.file_path for edit in edits}),
        )

    def _parse_unified_diff(self, raw_diff: str) -> PatchPlan:
        lines = raw_diff.splitlines()
        files: list[str] = []
        hunks: list[UnifiedDiffHunk] = []
        current_file = ""

        for index, line in enumerate(lines):
            if line.startswith("+++ "):
                path = line[4:].strip()
                current_file = _normalize_diff_path(path)
                if current_file and current_file != "/dev/null":
                    files.append(current_file)
            elif line.startswith("@@ "):
                if not current_file:
                    raise PatchParseError("Malformed unified diff: hunk without file header")
                match = _HUNK_RE.search(line)
                if not match:
                    raise PatchParseError(f"Malformed unified diff hunk: {line}")
                hunks.append(
                    UnifiedDiffHunk(
                        file_path=current_file,
                        header=line,
                        old_start=int(match.group("old_start")),
                        old_count=int(match.group("old_count") or "1"),
                        new_start=int(match.group("new_start")),
                        new_count=int(match.group("new_count") or "1"),
                    )
                )

        if not files:
            raise PatchParseError("Malformed unified diff: no changed files")
        if not hunks:
            raise PatchParseError("Malformed unified diff: no hunks")

        changed = sorted(set(files))
        return PatchPlan(
            patch_type=PatchType.UNIFIED_DIFF,
            raw_output=raw_diff,
            target_files=changed,
            unified_diff=UnifiedDiffPatch(raw_diff=raw_diff, files_changed=changed, hunks=hunks),
        )


def _looks_like_unified_diff(raw: str) -> bool:
    return ("--- " in raw and "+++ " in raw) or raw.startswith("diff --git")


def _normalize_diff_path(path: str) -> str:
    if path in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return Path(path).as_posix()
