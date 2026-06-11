from __future__ import annotations

from pathlib import Path

from rag.repo_indexer import RepoIndexer


def test_ignores_common_build_and_env_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "ignored.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Ignored.java").write_text("class X {}", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    files = RepoIndexer(tmp_path).scan()

    assert [item.relative_path for item in files] == ["src/app.py"]


def test_detects_supported_text_languages(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "Service.java").write_text("class Service {}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo", encoding="utf-8")

    files = RepoIndexer(tmp_path).scan()
    by_path = {item.relative_path: item for item in files}

    assert by_path["app.py"].language == "python"
    assert by_path["Service.java"].language == "java"
    assert by_path["README.md"].language == "markdown"
    assert by_path["app.py"].content_hash


def test_skips_large_binary_and_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "blob.py").write_bytes(b"\x00\x01\x02")
    (tmp_path / "large.py").write_text("x = 1\n" * 100, encoding="utf-8")

    first = RepoIndexer(tmp_path, max_file_size=128).scan()
    assert [item.relative_path for item in first] == ["app.py"]

    previous = {item.relative_path: item.content_hash for item in first}
    second = RepoIndexer(tmp_path, max_file_size=128).scan(previous_hashes=previous)
    assert second == []
