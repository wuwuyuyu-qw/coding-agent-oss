# Tool Calling Framework

## Why This Exists

The Self-Healing Coding Agent previously built one prompt and asked the LLM for
a patch. PR1 added repo-level RAG evidence. PR2 adds a small internal tool
calling framework so the agent can collect deterministic context before patch
generation without depending on MCP, paid services, or LLM-driven multi-step
tool use.

This PR implements a deterministic pre-patch tool pass. It is not an MCP
adapter, not a ReAct loop, and not a patch engine.

## Architecture

- `tools.models`: `ToolDefinition`, `Tool`, `ToolCall`, `ToolResult`, and
  `ToolTrace`.
- `tools.registry`: `ToolRegistry` for registration, lookup, enable, and
  disable. Re-registering the same name overwrites the prior tool.
- `tools.executor`: `ToolExecutor` for timeout, truncation, exception capture,
  and normalized `ToolResult` objects.
- `tools.builtin_tools`: built-in deterministic tools.
- `tools.context_builder`: prompt formatter for tool results.
- `tools.safety`: repo-local path and sensitive-file guards.
- `tools.errors`: explicit framework exceptions.

## Builtin Tools

- `list_files`: lists repo-relative files while ignoring generated directories.
- `read_file`: reads a repo-local file or line range.
- `search_code`: reuses PR1 `rag.HybridRetriever`.
- `grep_code`: regex search with graceful invalid-regex failure through the
  executor.
- `git_diff`: read-only `git diff --` with timeout and truncation.
- `run_tests`: allowlisted test execution only.

## Safety Boundaries

- All file paths are resolved under `repo_root`.
- Path traversal such as `../secret.txt` is rejected.
- `.git`, `.env`, private key files, token/secret-like files, and common key
  suffixes are blocked.
- Default tools are read-only.
- No tool uses `shell=True`.
- `run_tests` accepts only simple allowlisted commands:
  - `pytest`
  - `python -m pytest`
  - `unittest`
  - `python -m unittest`
  - `mvn test`
  - `gradle test`
  - `npm test`
- Tool output is truncated before it can enter a prompt.
- Tool failures become `ToolResult(success=False)` and do not fail the agent
  flow.

## Adding A Tool

Create a `ToolDefinition`, pair it with a handler, then register it:

```python
from tools.models import Tool, ToolDefinition
from tools.registry import ToolRegistry

def my_tool(arguments: dict) -> str:
    return "hello"

registry = ToolRegistry()
registry.register(
    Tool(
        definition=ToolDefinition(
            name="my_tool",
            description="Example tool",
            read_only=True,
            timeout_seconds=5,
            max_output_chars=2000,
        ),
        handler=my_tool,
    )
)
```

## Agent Integration

`core.nodes.generate_and_fix_node` builds prompt context in this order:

1. Original single-file repair prompt
2. `[Repo-level Evidence Context]` from PR1 RAG when available
3. `[Tool Calling Context]` from deterministic tools when `repo_root` exists

The optional state fields are:

- `tool_context`
- `tool_trace`
- `tool_metadata`
- `tool_results`
- `test_command`
- `enable_test_tool`

`run_tests` is not executed unless both `enable_test_tool` is true and
`test_command` is present.

## Demo

```bash
python examples/tool_calling_demo.py --repo-root ./examples/buggy_project
```

The demo runs `list_files`, `search_code`, and `read_file`.

## Current Limitations

- Deterministic pre-patch pass only; no LLM autonomous multi-turn tool calls.
- No MCP adapter.
- No patch engine or rollback/apply-patch tool.
- No sandbox integration for test execution beyond subprocess allowlisting.
- No persistent tool trace report yet.

## Future Extensions

- Planner-driven tool calls
- Multi-step ReAct loop
- MCP Adapter
- Patch Engine
- Tool trace report
- Sandbox integration

