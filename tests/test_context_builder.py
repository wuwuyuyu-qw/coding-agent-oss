from __future__ import annotations

from rag.context_builder import ContextBuilder
from rag.models import CodeChunk, RetrievalResult


def _result(chunk_id: str, score: float, content: str) -> RetrievalResult:
    chunk = CodeChunk(
        chunk_id=chunk_id,
        file_path="src/calculator.py",
        language="python",
        chunk_type="function",
        symbol_name="divide",
        start_line=10,
        end_line=12,
        content=content,
        content_hash=chunk_id,
        metadata={"is_test": False},
    )
    return RetrievalResult(
        chunk=chunk,
        score=score,
        score_breakdown={"path": score},
        matched_terms=["divide"],
        reason="file path appears in stack trace",
    )


def test_builds_bounded_context_with_trace_and_reason() -> None:
    results = [
        _result("same", 0.4, "def old():\n    pass"),
        _result("better", 2.0, "def divide(a, b):\n    return a / b"),
        _result("same", 0.4, "def old():\n    pass"),
    ]

    context = ContextBuilder(max_context_chars=1000).build_context(
        user_request="fix divide by zero",
        error_log="ZeroDivisionError",
        results=results,
        failing_tests=["test_divide_by_zero"],
        target_file="src/calculator.py",
    )

    assert "[User Request]" in context
    assert "[Retrieved Evidence 1]" in context
    assert "file: src/calculator.py" in context
    assert "lines: 10-12" in context
    assert "reason: file path appears in stack trace" in context
    assert "src/calculator.py:10-12" in context
    assert context.count("[Retrieved Evidence ") == 2
