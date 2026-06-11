"""Rollback helpers for Patch Engine."""

from __future__ import annotations

from pathlib import Path


class RollbackManager:
    """Restore file snapshots captured before patch application."""

    def __init__(self) -> None:
        self._snapshots: dict[Path, str] = {}

    def snapshot(self, path: Path) -> None:
        if path not in self._snapshots:
            self._snapshots[path] = path.read_text(encoding="utf-8") if path.exists() else ""

    def restore_all(self) -> list[str]:
        restored: list[str] = []
        for path, content in self._snapshots.items():
            path.write_text(content, encoding="utf-8")
            restored.append(str(path))
        return restored

