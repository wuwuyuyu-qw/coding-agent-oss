"""Shared data models for repo-level RAG."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FileMetadata:
    """Metadata for one repository file that is safe to index."""

    repo_root: str
    relative_path: str
    language: str
    file_size: int
    content_hash: str
    last_modified: float


@dataclass(frozen=True)
class CodeChunk:
    """A searchable piece of source code or documentation."""

    chunk_id: str
    file_path: str
    language: str
    chunk_type: str
    symbol_name: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    """A ranked retrieval result with an explainable score."""

    chunk: CodeChunk
    score: float
    score_breakdown: dict[str, float]
    matched_terms: list[str]
    reason: str


@dataclass(frozen=True)
class RagBuildResult:
    """Prompt-ready RAG context and operational metadata."""

    context: str
    metadata: dict[str, Any]
