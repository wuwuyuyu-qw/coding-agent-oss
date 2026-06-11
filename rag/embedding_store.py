"""Pluggable retrieval store with a local keyword fallback implementation."""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter

from .models import CodeChunk, RetrievalResult


class EmbeddingStore(ABC):
    """Minimal search interface that can be replaced by vector backends later."""

    @abstractmethod
    def add_chunks(self, chunks: list[CodeChunk]) -> None:
        """Add chunks to the searchable store."""

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        """Return ranked chunks for the query."""


class KeywordEmbeddingStore(EmbeddingStore):
    """Dependency-free lexical similarity store.

    This is intentionally small: it keeps tests and local demos working without
    API keys, model downloads, FAISS, or a database.
    """

    def __init__(self) -> None:
        self._chunks: dict[str, CodeChunk] = {}
        self._vectors: dict[str, Counter[str]] = {}

    def add_chunks(self, chunks: list[CodeChunk]) -> None:
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = Counter(_tokenize(_indexable_text(chunk)))

    def search(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        query_terms = Counter(_tokenize(query))
        if not query_terms:
            return []

        results: list[RetrievalResult] = []
        for chunk_id, chunk_terms in self._vectors.items():
            score, matched = _cosine_overlap(query_terms, chunk_terms)
            if score <= 0:
                continue
            chunk = self._chunks[chunk_id]
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=score,
                    score_breakdown={"text_similarity": score},
                    matched_terms=matched,
                    reason="lexical similarity with user request",
                )
            )

        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]


def _indexable_text(chunk: CodeChunk) -> str:
    return " ".join(
        [
            chunk.file_path,
            chunk.language,
            chunk.chunk_type,
            chunk.symbol_name,
            chunk.content,
        ]
    )


def _cosine_overlap(
    query_terms: Counter[str],
    chunk_terms: Counter[str],
) -> tuple[float, list[str]]:
    matched = sorted(set(query_terms) & set(chunk_terms))
    if not matched:
        return 0.0, []

    dot = sum(query_terms[term] * chunk_terms[term] for term in matched)
    query_norm = math.sqrt(sum(value * value for value in query_terms.values()))
    chunk_norm = math.sqrt(sum(value * value for value in chunk_terms.values()))
    if query_norm == 0 or chunk_norm == 0:
        return 0.0, matched
    return dot / (query_norm * chunk_norm), matched


def _tokenize(text: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())
    terms: list[str] = []
    for term in raw_terms:
        terms.append(term)
        terms.extend(part for part in term.split("_") if part and part != term)
    return terms
