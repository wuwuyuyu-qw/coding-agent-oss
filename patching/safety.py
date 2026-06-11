"""Safety helpers for Patch Engine repo-local file access."""

from __future__ import annotations

from pathlib import Path

from .errors import PatchValidationError

SENSITIVE_FILENAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_dsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def resolve_repo_root(repo_root: str) -> Path:
    root = Path(repo_root).resolve()
    if not root.exists() or not root.is_dir():
        raise PatchValidationError(f"repo_root does not exist: {repo_root}")
    return root


def resolve_repo_path(repo_root: str, file_path: str) -> Path:
    root = resolve_repo_root(repo_root)
    candidate = (root / file_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PatchValidationError("path_traversal") from exc
    if is_sensitive_path(candidate, root):
        raise PatchValidationError(f"sensitive_path:{file_path}")
    return candidate


def is_sensitive_path(path: Path, repo_root: Path) -> bool:
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

