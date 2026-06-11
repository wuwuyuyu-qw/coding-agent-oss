"""Repository scanner for local, dependency-free repo-level RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping

from .models import FileMetadata


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "dist",
    ".venv",
    "venv",
    "logs",
    ".pytest_cache",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".java": "java",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".txt": "text",
}


class RepoIndexer:
    """Scan a local repository and return files that are safe to chunk."""

    def __init__(
        self,
        repo_root: str | os.PathLike[str],
        *,
        max_file_size: int = 256 * 1024,
        ignore_dirs: Iterable[str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_file_size = max_file_size
        self.ignore_dirs = set(ignore_dirs or DEFAULT_IGNORE_DIRS)

    def scan(
        self,
        *,
        previous_hashes: Mapping[str, str] | None = None,
    ) -> list[FileMetadata]:
        """Return indexable files, skipping unchanged paths when hashes are given."""

        if not self.repo_root.exists() or not self.repo_root.is_dir():
            raise FileNotFoundError(f"repo_root does not exist: {self.repo_root}")

        previous_hashes = previous_hashes or {}
        files: list[FileMetadata] = []

        for root, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [
                dirname for dirname in dirnames if not self._should_ignore_dir(dirname)
            ]
            for filename in filenames:
                path = Path(root) / filename
                metadata = self._metadata_for_path(path)
                if metadata is None:
                    continue
                if previous_hashes.get(metadata.relative_path) == metadata.content_hash:
                    continue
                files.append(metadata)

        return sorted(files, key=lambda item: item.relative_path)

    def _metadata_for_path(self, path: Path) -> FileMetadata | None:
        suffix = path.suffix.lower()
        language = LANGUAGE_BY_SUFFIX.get(suffix)
        if language is None:
            return None

        try:
            stat = path.stat()
        except OSError:
            return None

        if stat.st_size > self.max_file_size or stat.st_size < 0:
            return None

        try:
            raw = path.read_bytes()
        except OSError:
            return None

        if _looks_binary(raw):
            return None

        relative_path = path.relative_to(self.repo_root).as_posix()
        return FileMetadata(
            repo_root=str(self.repo_root),
            relative_path=relative_path,
            language=language,
            file_size=stat.st_size,
            content_hash=hashlib.sha256(raw).hexdigest(),
            last_modified=stat.st_mtime,
        )

    def _should_ignore_dir(self, dirname: str) -> bool:
        return dirname in self.ignore_dirs


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw[:2048]:
        return True
    try:
        raw[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _main() -> None:  # pragma: no cover - exercised by demo command.
    parser = argparse.ArgumentParser(description="Index repository files for RAG.")
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args()

    indexer = RepoIndexer(args.repo_root)
    files = indexer.scan()
    payload = {
        "repo_root": str(indexer.repo_root),
        "file_count": len(files),
        "files": [file.__dict__ for file in files],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    _main()
