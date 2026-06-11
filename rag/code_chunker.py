"""Code chunking for Python, Java, and text-like repository files."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from .models import CodeChunk, FileMetadata


class CodeChunker:
    """Split repository files into symbol-aware retrieval chunks."""

    def __init__(self, *, window_lines: int = 120, overlap_lines: int = 20) -> None:
        if window_lines <= 0:
            raise ValueError("window_lines must be positive")
        if overlap_lines < 0 or overlap_lines >= window_lines:
            raise ValueError("overlap_lines must be smaller than window_lines")
        self.window_lines = window_lines
        self.overlap_lines = overlap_lines

    def chunk_file(self, metadata: FileMetadata) -> list[CodeChunk]:
        path = Path(metadata.repo_root) / metadata.relative_path
        content = path.read_text(encoding="utf-8")
        return self.chunk_text(
            content,
            file_path=metadata.relative_path,
            language=metadata.language,
        )

    def chunk_text(
        self,
        content: str,
        *,
        file_path: str,
        language: str,
    ) -> list[CodeChunk]:
        lines = content.splitlines()
        if language == "python":
            chunks = self._chunk_python(content, lines, file_path, language)
        elif language == "java":
            chunks = self._chunk_java(content, lines, file_path, language)
        else:
            chunks = []

        if chunks:
            return chunks
        return self._chunk_by_window(lines, file_path=file_path, language=language)

    def _chunk_python(
        self,
        content: str,
        lines: list[str],
        file_path: str,
        language: str,
    ) -> list[CodeChunk]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        chunks: list[CodeChunk] = []
        parent_stack: list[str] = []

        def visit(node: ast.AST) -> None:
            if isinstance(node, ast.ClassDef):
                chunks.append(
                    self._make_chunk(
                        lines,
                        file_path=file_path,
                        language=language,
                        chunk_type="class",
                        symbol_name=".".join(parent_stack + [node.name]),
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                    )
                )
                parent_stack.append(node.name)
                for child in node.body:
                    visit(child)
                parent_stack.pop()
                return

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = ".".join(parent_stack + [node.name])
                chunks.append(
                    self._make_chunk(
                        lines,
                        file_path=file_path,
                        language=language,
                        chunk_type="method" if parent_stack else "function",
                        symbol_name=symbol,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                    )
                )
                return

            for child in ast.iter_child_nodes(node):
                visit(child)

        for child in tree.body:
            visit(child)
        return chunks

    def _chunk_java(
        self,
        content: str,
        lines: list[str],
        file_path: str,
        language: str,
    ) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []
        class_ranges: list[tuple[str, int, int]] = []

        for index, line in enumerate(lines, start=1):
            class_match = re.search(r"\b(class|interface|enum)\s+([A-Za-z_]\w*)", line)
            if class_match:
                end_line = _find_java_block_end(lines, index)
                class_name = class_match.group(2)
                class_ranges.append((class_name, index, end_line))
                chunks.append(
                    self._make_chunk(
                        lines,
                        file_path=file_path,
                        language=language,
                        chunk_type="class",
                        symbol_name=class_name,
                        start_line=index,
                        end_line=end_line,
                    )
                )

            method_match = re.search(
                r"\b(?:public|private|protected|static|final|synchronized|abstract|\s)+"
                r"[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws [^{]+)?\{",
                line,
            )
            if method_match and not class_match:
                method_name = method_match.group(1)
                if method_name in {"if", "for", "while", "switch", "catch"}:
                    continue
                end_line = _find_java_block_end(lines, index)
                class_name = _containing_class(class_ranges, index)
                symbol = f"{class_name}.{method_name}" if class_name else method_name
                chunks.append(
                    self._make_chunk(
                        lines,
                        file_path=file_path,
                        language=language,
                        chunk_type="method",
                        symbol_name=symbol,
                        start_line=index,
                        end_line=end_line,
                    )
                )

        return chunks

    def _chunk_by_window(
        self,
        lines: list[str],
        *,
        file_path: str,
        language: str,
    ) -> list[CodeChunk]:
        if not lines:
            return [
                self._make_chunk(
                    [""],
                    file_path=file_path,
                    language=language,
                    chunk_type="file",
                    symbol_name="",
                    start_line=1,
                    end_line=1,
                )
            ]

        chunks: list[CodeChunk] = []
        step = self.window_lines - self.overlap_lines
        start = 1
        while start <= len(lines):
            end = min(len(lines), start + self.window_lines - 1)
            chunks.append(
                self._make_chunk(
                    lines,
                    file_path=file_path,
                    language=language,
                    chunk_type="file" if start == 1 and end == len(lines) else "block",
                    symbol_name="",
                    start_line=start,
                    end_line=end,
                )
            )
            if end == len(lines):
                break
            start += step
        return chunks

    def _make_chunk(
        self,
        lines: list[str],
        *,
        file_path: str,
        language: str,
        chunk_type: str,
        symbol_name: str,
        start_line: int,
        end_line: int,
    ) -> CodeChunk:
        content = "\n".join(lines[start_line - 1 : end_line])
        digest = hashlib.sha256(
            f"{file_path}:{start_line}:{end_line}:{content}".encode("utf-8")
        ).hexdigest()
        return CodeChunk(
            chunk_id=f"{file_path}:{start_line}-{end_line}:{digest[:12]}",
            file_path=file_path,
            language=language,
            chunk_type=chunk_type,
            symbol_name=symbol_name,
            start_line=start_line,
            end_line=end_line,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            metadata={"is_test": _is_test_file(file_path)},
        )


def _find_java_block_end(lines: list[str], start_line: int) -> int:
    depth = 0
    seen_open = False
    for index in range(start_line, len(lines) + 1):
        line = lines[index - 1]
        depth += line.count("{")
        if "{" in line:
            seen_open = True
        depth -= line.count("}")
        if seen_open and depth <= 0:
            return index
    return len(lines)


def _containing_class(class_ranges: list[tuple[str, int, int]], line: int) -> str:
    for class_name, start, end in reversed(class_ranges):
        if start <= line <= end:
            return class_name
    return ""


def _is_test_file(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    name = Path(normalized).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("Test.java")
        or "/tests/" in normalized
    )
