"""Safety helpers for repo-local tool operations."""

from __future__ import annotations

from pathlib import Path

from .errors import ToolSafetyError


SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}

IGNORED_DIRS = {
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


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root).resolve()
    if not root.exists() or not root.is_dir():
        raise ToolSafetyError(f"repo_root does not exist: {repo_root}")
    return root


def resolve_repo_path(repo_root: str, relative_path: str) -> Path:
    root = resolve_repo_root(repo_root)
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ToolSafetyError("Path escapes repo_root") from exc
    if _is_sensitive_path(candidate, root):
        raise ToolSafetyError(f"Refusing to access sensitive path: {relative_path}")
    return candidate


def is_ignored_path(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in relative.parts)


def _is_sensitive_path(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True
    if ".git" in relative.parts:
        return True
    name = path.name.lower()
    if name in SENSITIVE_FILENAMES:
        return True
    if "token" in name or "secret" in name:
        return True
    return path.suffix.lower() in SENSITIVE_SUFFIXES

