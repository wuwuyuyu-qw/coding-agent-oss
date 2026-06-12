# MCP Server Adapter

PR5 adds a minimal MCP server adapter for the existing internal Tool Calling
Framework. The adapter exposes selected repository analysis tools to external
MCP clients without rewriting the underlying tools.

## Relationship To Tool Calling Framework

The MCP layer is protocol glue:

```text
MCP Client
-> mcp_adapter.server
-> MCPToolAdapter
-> tools.ToolExecutor
-> tools.ToolRegistry
-> builtin tools
```

All file access, path checks, timeouts, output truncation, and test-command
allowlisting remain owned by the existing `tools/` package.

## Exposed Tools

- `list_files`
- `read_file`
- `search_code`
- `grep_code`
- `git_diff`
- `run_tests`, only when `--enable-test-tool` is passed

`apply_patch` is not exposed. It mutates source files and needs a separate
confirmation workflow before it should be made available through MCP.

## Resources

- `repo://tool-catalog`
- `repo://summary`

Resources are read-only and return short JSON payloads.

## Start The Server

```bash
python -m mcp_adapter.server --repo-root . --transport stdio
```

Enable the test tool explicitly:

```bash
python -m mcp_adapter.server --repo-root . --transport stdio --enable-test-tool
```

The MCP Python SDK is imported lazily. If it is not installed, normal Agent,
RAG, Tool Calling, Patch Engine, and Benchmark code still imports and tests
normally. Starting the server prints a clear dependency error.

## Example Client Config

See `examples/mcp_client_config.example.json`. Replace
`/path/to/coding-agent-oss` with the local repository path.

## Safety Boundaries

- `repo_root` is fixed at server startup.
- MCP tool inputs cannot provide or override `repo_root`.
- Path traversal is blocked by the existing tool safety helpers.
- `.git`, `.env`, private keys, token-like files, and secret-like files are blocked.
- `run_tests` is disabled by default.
- `run_tests` still uses the existing allowlist.
- Subprocess tools use list args and `shell=False`.
- Tool execution has timeouts.
- Tool output is truncated before returning to MCP.
- `apply_patch` is not exposed.

## Current Limits

- Only stdio transport is implemented.
- No authentication system is included.
- No remote HTTP server is included.
- No MCP client is included.
- `apply_patch` remains unavailable over MCP.

## Future Extensions

- Streamable HTTP transport.
- More read-only resources, such as patch audit and benchmark reports.
- Client integration tests.
- Optional confirmation workflow for risky tools.
