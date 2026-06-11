"""Build prompt-ready evidence context from retrieval results."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import RetrievalResult


class ContextBuilder:
    """Assemble bounded RAG context for an LLM prompt."""

    def __init__(self, *, max_context_chars: int = 8000) -> None:
        self.max_context_chars = max_context_chars

    def build_context(
        self,
        *,
        user_request: str,
        error_log: str = "",
        results: list[RetrievalResult],
        failing_tests: Iterable[str] | str | None = None,
        target_file: str = "",
    ) -> str:
        selected = self._dedupe(results)
        candidate_files = []
        relevant_tests = []
        for result in selected:
            if result.chunk.file_path not in candidate_files:
                candidate_files.append(result.chunk.file_path)
            if result.chunk.metadata.get("is_test"):
                relevant_tests.append(result.chunk.file_path)

        sections = [
            "[User Request]",
            user_request.strip() or "(empty)",
            "",
            "[Error Log Summary]",
            _truncate(error_log.strip(), 1200) if error_log.strip() else "(empty)",
            "",
            "[Relevant Tests]",
            "\n".join(_as_list(failing_tests) or relevant_tests or ["(none)"]),
            "",
            "[Candidate Files]",
            "\n".join(candidate_files or ([target_file] if target_file else ["(none)"])),
            "",
            "[Retrieved Evidence]",
        ]
        context = "\n".join(sections)

        evidence_index = 1
        for result in selected:
            block = self._format_evidence(evidence_index, result)
            if len(context) + len(block) + 2 > self.max_context_chars:
                break
            context = f"{context}\n\n{block}"
            evidence_index += 1

        return context[: self.max_context_chars]

    def _dedupe(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        seen: set[str] = set()
        deduped: list[RetrievalResult] = []
        for result in sorted(results, key=lambda item: item.score, reverse=True):
            if result.chunk.chunk_id in seen:
                continue
            seen.add(result.chunk.chunk_id)
            deduped.append(result)
        return deduped

    def _format_evidence(self, index: int, result: RetrievalResult) -> str:
        chunk = result.chunk
        symbol = chunk.symbol_name or "(none)"
        reason = result.reason or "retrieved by hybrid search"
        return "\n".join(
            [
                f"[Retrieved Evidence {index}]",
                f"file: {chunk.file_path}",
                f"lines: {chunk.start_line}-{chunk.end_line}",
                f"symbol: {symbol}",
                f"reason: {reason}",
                f"score: {result.score:.4f}",
                "content:",
                chunk.content,
                "",
                "[Evidence Trace]",
                f"{Path(chunk.file_path).as_posix()}:{chunk.start_line}-{chunk.end_line}",
            ]
        )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + "\n...<truncated>"


def _as_list(value: Iterable[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if item]
