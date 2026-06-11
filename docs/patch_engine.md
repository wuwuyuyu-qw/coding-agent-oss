# Patch Engine + Diff Audit

## Why Patch Engine

The original agent accepted LLM Search/Replace output and merged it directly
into `current_code`. PR3 adds a repo-aware Patch Engine so patch output can be
parsed, validated, dry-run, applied, rolled back, and audited with git diff
metadata before the result is recorded in state.

This PR keeps the original single-file flow intact when `repo_root` is missing.

## Patch Types

### Search/Replace

The existing fenced format remains supported:

````text
```search
old code
```
```replace
new code
```
````

Search/Replace patches are precise string edits. In repo-aware mode, each
search string must match exactly once.

### Unified Diff

Basic unified diff structure is parsed:

```diff
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -1 +1 @@
-old
+new
```

This parser is intentionally lightweight. Application uses `git apply --check`
and `git apply` when `repo_root` is a git repository.

## Architecture

- `patching.models`: dataclasses for plans, edits, validation, apply result, and audit.
- `patching.parser`: parses Search/Replace and basic unified diff output.
- `patching.validator`: validates paths, limits, unique matches, and risk warnings.
- `patching.applier`: performs dry-run/apply and rollback for Search/Replace; uses git for unified diff.
- `patching.diff_audit`: captures `git diff --stat`, `git diff --`, changed files, line counts, and suspicious signals.
- `patching.rollback`: restores captured file snapshots on apply failure.
- `patching.safety`: repo-local path and sensitive-file checks.
- `patching.errors`: patch-specific exceptions.

## Flow

```text
LLM Patch Output
-> PatchParser
-> PatchValidator
-> DryRunApply
-> ApplyPatch
-> GitDiffAudit
-> patch_* state fields
```

Failure path:

```text
failure reason
-> rollback when partial writes happened
-> PatchApplyResult(success=false)
-> Actor keeps previous code and reports Patch Merge Failed
```

## Safety Boundaries

- File paths must resolve under `repo_root`.
- Path traversal is rejected.
- `.git`, `.env`, private keys, token/secret-like files, and key suffixes are blocked.
- Patch file count defaults to at most 20 files.
- Patch size defaults to at most 200 KB.
- Git subprocess calls use list args, timeout, and `shell=False`.
- Diff output is truncated before storage.

## Agent Integration

`core.nodes.generate_and_fix_node` uses Patch Engine only when both `repo_root`
and `target_file` are present. Without them, it calls the existing
`merge_patches_into_code` single-file flow.

Successful repo-aware application writes:

- `patch_plan`
- `patch_validation`
- `patch_apply_result`
- `patch_audit`

PR1 `[Repo-level Evidence Context]` and PR2 `[Tool Calling Context]` remain
unchanged.

## Current Limitations

- Unified diff parsing is basic.
- Complex conflict resolution is not implemented.
- `apply_patch` is not exposed as an autonomous LLM tool.
- No benchmark integration yet.

## Future Extensions

- Planner-driven patch apply
- Patch confidence score
- Semantic diff
- AST-level patch
- Sandbox-based patch validation
- Benchmark report
