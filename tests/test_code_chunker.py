from __future__ import annotations

from rag.code_chunker import CodeChunker


def test_chunks_python_functions_classes_and_methods() -> None:
    source = """\
class Calculator:
    def add(self, a, b):
        return a + b

def normalize(value):
    return value.strip()
"""

    chunks = CodeChunker().chunk_text(
        source,
        file_path="src/calculator.py",
        language="python",
    )

    symbols = {chunk.symbol_name: chunk for chunk in chunks}
    assert symbols["Calculator"].chunk_type == "class"
    assert symbols["Calculator.add"].chunk_type == "method"
    assert symbols["normalize"].chunk_type == "function"
    assert symbols["normalize"].start_line == 5


def test_chunks_java_class_and_method() -> None:
    source = """\
package demo;

public class Parser {
    public String parse(String raw) {
        return raw.trim();
    }
}
"""

    chunks = CodeChunker().chunk_text(
        source,
        file_path="src/main/java/demo/Parser.java",
        language="java",
    )

    symbols = {chunk.symbol_name: chunk for chunk in chunks}
    assert symbols["Parser"].chunk_type == "class"
    assert symbols["Parser.parse"].chunk_type == "method"
    assert "raw.trim" in symbols["Parser.parse"].content


def test_falls_back_to_window_chunks_and_marks_tests() -> None:
    content = "\n".join(f"line {i}" for i in range(1, 6))

    chunks = CodeChunker(window_lines=3, overlap_lines=1).chunk_text(
        content,
        file_path="tests/test_notes.md",
        language="markdown",
    )

    assert len(chunks) == 2
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3
    assert chunks[0].metadata["is_test"] is True
