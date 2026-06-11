"""Hybrid repo-level retrieval using lexical, path, symbol, and test signals."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Iterable

from .code_chunker import CodeChunker
from .embedding_store import KeywordEmbeddingStore
from .models import CodeChunk, RetrievalResult
from .repo_indexer import RepoIndexer


class HybridRetriever:
    """Build a local repo index and return explainable ranked code chunks."""

    def __init__(
        self,
        *,
        indexer: RepoIndexer | None = None,
        chunker: CodeChunker | None = None,
        store: KeywordEmbeddingStore | None = None,
    ) -> None:
        self.indexer = indexer
        self.chunker = chunker or CodeChunker()
        self.store = store or KeywordEmbeddingStore()
        self.indexed_file_count = 0
        self.indexed_chunk_count = 0

    def retrieve(
        self,
        *,
        user_request: str = "",
        error_log: str = "",
        failing_tests: Iterable[str] | str | None = None,
        target_file: str = "",
        repo_root: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        indexer = self.indexer or RepoIndexer(repo_root)
        files = indexer.scan()
        chunks: list[CodeChunk] = []
        for file_metadata in files:
            chunks.extend(self.chunker.chunk_file(file_metadata))

        self.indexed_file_count = len(files)
        self.indexed_chunk_count = len(chunks)
        self.store.add_chunks(chunks)

        query = _join_query(user_request, error_log, failing_tests, target_file)
        candidates = self.store.search(query, top_k=max(top_k * 4, 20))
        if not candidates:
            return []

        stack_files = _extract_stack_files(error_log)
        stack_symbols = _extract_symbols(error_log)
        failing_test_terms = _as_list(failing_tests)
        target_name = Path(target_file).name if target_file else ""

        reranked: list[RetrievalResult] = []
        for result in candidates:
            breakdown = dict(result.score_breakdown)
            matched_terms = set(result.matched_terms)
            chunk = result.chunk

            keyword_score = _keyword_score(chunk, user_request)
            path_score = _path_score(chunk, stack_files, target_file)
            symbol_score = _symbol_score(chunk, stack_symbols, query)
            test_score = _test_score(chunk, failing_test_terms)
            target_score = 1.0 if target_name and Path(chunk.file_path).name == target_name else 0.0

            breakdown.update(
                {
                    "keyword": keyword_score,
                    "path": path_score,
                    "symbol": symbol_score,
                    "test_priority": test_score,
                    "target_file": target_score,
                }
            )
            score = (
                result.score
                + keyword_score
                + path_score
                + symbol_score
                + test_score
                + target_score
            )
            reasons = _reasons(
                text_similarity=result.score,
                keyword_score=keyword_score,
                path_score=path_score,
                symbol_score=symbol_score,
                test_score=test_score,
                target_score=target_score,
            )
            matched_terms.update(_matched_stack_terms(chunk, stack_files, stack_symbols))
            reranked.append(
                RetrievalResult(
                    chunk=chunk,
                    score=score,
                    score_breakdown=breakdown,
                    matched_terms=sorted(matched_terms),
                    reason="; ".join(reasons) if reasons else result.reason,
                )
            )

        return sorted(reranked, key=lambda item: item.score, reverse=True)[:top_k]


def _join_query(
    user_request: str,
    error_log: str,
    failing_tests: Iterable[str] | str | None,
    target_file: str,
) -> str:
    return "\n".join([user_request, error_log, "\n".join(_as_list(failing_tests)), target_file])


def _as_list(value: Iterable[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if item]


def _extract_stack_files(error_log: str) -> list[str]:
    files = re.findall(r'File "([^"]+)"', error_log)
    files.extend(re.findall(r"([A-Za-z0-9_./\\-]+\.(?:py|java))", error_log))
    return sorted({Path(file).name for file in files} | {file.replace("\\", "/") for file in files})


def _extract_symbols(error_log: str) -> list[str]:
    symbols = re.findall(r"\bin ([A-Za-z_][A-Za-z0-9_]*)", error_log)
    symbols.extend(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b", error_log))
    return sorted(set(symbols))


def _keyword_score(chunk: CodeChunk, user_request: str) -> float:
    terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", user_request.lower()))
    if not terms:
        return 0.0
    haystack = f"{chunk.file_path} {chunk.symbol_name} {chunk.content}".lower()
    matches = sum(1 for term in terms if term in haystack)
    return min(matches / max(len(terms), 1), 1.0) * 0.4


def _path_score(chunk: CodeChunk, stack_files: list[str], target_file: str) -> float:
    normalized_path = chunk.file_path.replace("\\", "/")
    chunk_name = Path(normalized_path).name
    score = 0.0
    for stack_file in stack_files:
        normalized_stack = stack_file.replace("\\", "/")
        if stack_file and (
            normalized_stack == normalized_path
            or Path(normalized_stack).name == chunk_name
        ):
            score = max(score, 2.0)
    if target_file and (
        target_file.replace("\\", "/") in normalized_path
        or Path(target_file).name == chunk_name
    ):
        score = max(score, 1.0)
    return score


def _symbol_score(chunk: CodeChunk, stack_symbols: list[str], query: str) -> float:
    if not chunk.symbol_name:
        return 0.0
    symbol_lower = chunk.symbol_name.lower()
    score = 0.0
    for symbol in stack_symbols:
        if symbol.lower() in symbol_lower:
            score = max(score, 1.0)
    if chunk.symbol_name.lower() in query.lower():
        score = max(score, 0.8)
    return score


def _test_score(chunk: CodeChunk, failing_tests: list[str]) -> float:
    score = 0.2 if chunk.metadata.get("is_test") else 0.0
    haystack = f"{chunk.file_path} {chunk.symbol_name} {chunk.content}".lower()
    for test_name in failing_tests:
        if test_name.lower() in haystack:
            score = max(score, 1.0)
    return score


def _matched_stack_terms(
    chunk: CodeChunk,
    stack_files: list[str],
    stack_symbols: list[str],
) -> list[str]:
    haystack = f"{chunk.file_path} {chunk.symbol_name}".lower()
    terms = []
    for term in stack_files + stack_symbols:
        if term and term.lower() in haystack:
            terms.append(term)
    return terms


def _reasons(**scores: float) -> list[str]:
    labels = {
        "text_similarity": "lexical similarity with user request",
        "keyword_score": "matched request keywords",
        "path_score": "file path appears in stack trace",
        "symbol_score": "symbol name matched query",
        "test_score": "matched failing test name",
        "target_score": "matched target file",
    }
    return [labels[name] for name, score in scores.items() if score > 0]


def _main() -> None:  # pragma: no cover - exercised by demo command.
    parser = argparse.ArgumentParser(description="Retrieve repo-level RAG evidence.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--error-log", default="")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    error_log = ""
    if args.error_log:
        error_path = Path(args.error_log)
        error_log = error_path.read_text(encoding="utf-8") if error_path.exists() else args.error_log

    start = time.perf_counter()
    retriever = HybridRetriever()
    results = retriever.retrieve(
        user_request=args.query,
        error_log=error_log,
        repo_root=args.repo_root,
        top_k=args.top_k,
    )
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    payload = {
        "indexed_files": retriever.indexed_file_count,
        "indexed_chunks": retriever.indexed_chunk_count,
        "retrieved_chunks": len(results),
        "elapsed_ms": elapsed_ms,
        "results": [
            {
                "file": item.chunk.file_path,
                "lines": f"{item.chunk.start_line}-{item.chunk.end_line}",
                "symbol": item.chunk.symbol_name,
                "score": round(item.score, 4),
                "reason": item.reason,
            }
            for item in results
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    _main()
