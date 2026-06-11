from __future__ import annotations

from pathlib import Path

from rag.hybrid_retriever import HybridRetriever


def test_retrieves_chunk_by_stack_trace_file_and_symbol(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calculator.py").write_text(
        """\
def divide(a, b):
    return a / b

def add(a, b):
    return a + b
""",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calculator.py").write_text(
        """\
from src.calculator import divide

def test_divide_by_zero():
    assert divide(1, 0) == 0
""",
        encoding="utf-8",
    )

    error_log = """\
Traceback (most recent call last):
  File "src/calculator.py", line 2, in divide
ZeroDivisionError: division by zero
"""

    results = HybridRetriever().retrieve(
        user_request="fix divide by zero",
        error_log=error_log,
        failing_tests=["test_divide_by_zero"],
        target_file="src/calculator.py",
        repo_root=str(tmp_path),
        top_k=3,
    )

    assert results
    assert results[0].chunk.file_path == "src/calculator.py"
    assert results[0].chunk.symbol_name == "divide"
    assert "file path appears in stack trace" in results[0].reason
    assert "divide" in results[0].matched_terms
