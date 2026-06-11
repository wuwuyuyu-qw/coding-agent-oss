# Repo-level RAG

## Design Goals

Repo-level RAG adds repository-aware evidence retrieval to the existing
Self-Healing Coding Agent. The first phase keeps the current single-file
repair loop intact and only adds optional context when `repo_root` is provided.

The module is local-first:

- no external API key is required;
- no paid embedding service is required;
- retrieval failures fall back to the original prompt flow;
- RAG is not coupled to the LLM provider.

## Module Layout

- `rag.models`: shared dataclasses for file metadata, code chunks, and results.
- `rag.repo_indexer`: scans local repos and filters unsupported, binary, ignored,
  or oversized files.
- `rag.code_chunker`: splits files into Python AST chunks, Java lightweight
  class/method chunks, or line-window chunks.
- `rag.embedding_store`: defines the pluggable `EmbeddingStore` interface and a
  local keyword similarity fallback.
- `rag.hybrid_retriever`: combines lexical, path, symbol, test, and target-file
  signals.
- `rag.context_builder`: formats bounded Evidence Context for the LLM prompt.

## Indexing Flow

1. `RepoIndexer(repo_root).scan()` walks the repository.
2. Common generated directories are ignored: `.git`, `.idea`, `.vscode`,
   `__pycache__`, `node_modules`, `target`, `build`, `dist`, `.venv`, `venv`,
   `logs`, and `.pytest_cache`.
3. Files over 256 KB and binary-looking files are skipped.
4. Supported text files receive metadata: repo root, relative path, language,
   size, content hash, and last-modified timestamp.
5. A simple incremental API is available through `previous_hashes`; unchanged
   files are skipped when their content hash matches.

## Retrieval Flow

1. The retriever builds chunks from indexed files.
2. The local keyword store ranks chunks by lexical similarity.
3. Hybrid re-ranking adds signals from:
   - user request terms;
   - stack-trace file paths;
   - stack-trace symbols and exception names;
   - failing test names;
   - test-file priority;
   - target-file matches.
4. Each result includes a score breakdown, matched terms, and a human-readable
   reason.

## Agent Integration

`core.nodes.generate_and_fix_node` now checks optional state fields:

- `repo_root`
- `user_request`
- `failing_tests`
- `target_file`

When `repo_root` exists, the node retrieves evidence and appends a
`[Repo-level Evidence Context]` section to the existing user prompt. When the
path is missing or retrieval fails, the node logs metadata and continues with
the original single-file prompt.

The existing fields remain the repair contract:

- `original_code`
- `current_code`
- `test_code`
- `error_log`

## Local Demo

Index the toy repo:

```bash
python -m rag.repo_indexer --repo-root ./examples/buggy_project
```

Retrieve evidence:

```bash
python -m rag.hybrid_retriever --repo-root ./examples/buggy_project --query "fix divide by zero" --error-log ./examples/error.log --top-k 5
```

On Windows in this Codex desktop runtime, replace `python` with the bundled
Python path if needed.

## Current Limitations

- The fallback store is lexical, not semantic.
- Java parsing is intentionally lightweight and approximate.
- Python chunking uses AST but does not yet build call graphs.
- No persistent index is stored between runs.
- No repo map or dependency graph is generated yet.

## Future Extensions

- OpenAI Embeddings
- FAISS / Chroma
- Tree-sitter
- BM25
- MCP Tool Adapter
- Repo Map
- Call Graph
