# Specback Search MCP Server (specback-search-mcp)

> **Documentation**: [English](10-specback-search-mcp.md) · [日本語](../ja/10-specback-search-mcp.md)

## Overview

Generated specback data (`source-map.json`, `trace.json`, `inventory.json`,
`questions.json`, `drift-report.json`) is normally queried through the
`build-search-index.py` CLI. Issue #270 adds a **Model Context Protocol (MCP)
stdio server** that wraps that CLI, so AI agents (Claude Code, Cursor, Hermes,
…) can query the data natively through MCP tools instead of shelling out to a
CLI.

The server is **stdlib-only** (Python 3.10+, no external dependencies): it
speaks the MCP stdio transport (newline-delimited JSON-RPC 2.0 over
stdin/stdout) directly, with no `mcp` SDK. This keeps the install surface
zero and confines MCP ecosystem churn to one small file.

## Tools

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `specback_search` | Search source units by name/path, optionally filtered by confidence, chapter, or role | `query` (optional — at least one of `query`/`chapter`/`role`/`confidence` required), `specback_dir` (default `.specback`), `confidence` (🟢/🟡/🔴), `chapter`, `role`, `format` (`text`/`json`) |
| `specback_uncovered` | List source units not covered by any spec chapter | `specback_dir`, `role`, `confidence`, `format` |
| `specback_drift` | Show the latest drift report summary | `specback_dir`, `format` |
| `specback_questions` | Show unresolved (or all) questions | `specback_dir`, `mode` (`open`/`all`, default `open`), `format` |

All tools return `text` (human-readable, same formatting as the CLI) or
`json` (machine-readable) output. Every tool takes `specback_dir` so one
server process can query multiple projects. Results are capped at 200 source
units per call (text output notes truncation); `query` is capped at 500 chars
and filters at 200 chars; artifact files over 50 MiB are rejected.

## Running the server

```bash
python3 skills/specback-search/scripts/specback-search-mcp.py
```

The server reads JSON-RPC messages from stdin and writes responses to stdout
until stdin closes (exit 0). Logs/errors go to stderr only — stdout is
reserved for the protocol.

### Transport smoke test

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
| python3 skills/specback-search/scripts/specback-search-mcp.py
```

Expected: an `initialize` result with `serverInfo.name = "specback-search"`,
then a `tools/list` result listing the 4 tools above.

## Client configuration

### Claude Code

`.mcp.json` at the project root (or user scope):

```json
{
  "mcpServers": {
    "specback-search": {
      "command": "python3",
      "args": ["/absolute/path/to/skills/specback-search/scripts/specback-search-mcp.py"]
    }
  }
}
```

### Cursor

`.cursor/mcp.json` (project) or global MCP config — same JSON shape as above.

### Hermes Agent

Add to `~/.hermes/config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  specback-search:
    command: python3
    args: ["/absolute/path/to/skills/specback-search/scripts/specback-search-mcp.py"]
```

Restart Hermes; the 4 tools become available as `mcp_specback_search_*`.

## Protocol notes

- **Transport**: one JSON-RPC 2.0 object per line, newline-delimited, over
  stdio (the MCP stdio transport). No Content-Length framing.
- **initialize**: the server echoes the client's requested `protocolVersion`
  (fallback `2024-11-05`) and advertises `capabilities.tools.listChanged =
  false`.
- **Error codes**: `-32700` parse error, `-32601` method not found, `-32602`
  invalid params, `-32603` internal error.
- **Failure semantics**: a tool call never kills the server. Missing
  `specback_dir`, missing `source-map.json` / `trace.json` (specback not
  run yet), **corrupt (invalid JSON) artifacts**, **FIFO/socket/device
  artifacts**, or oversized artifacts all return a tool result with
  `isError: true` and an actionable message. Malformed stdin (bad JSON,
  invalid UTF-8, deeply nested messages, oversized lines) is answered with a
  protocol error (`-32700` / `-32600`) and the server keeps serving.
- **Notifications** (messages without an `id`, e.g.
  `notifications/initialized`, `notifications/cancelled`) get no response.

## Trust boundary

The server is a **read-only local tool** that runs with the same privileges as
the invoking user. It reads **any path the client names** in `specback_dir`
(within that directory: `source-map.json`, `trace.json`, `inventory.json`,
`questions.json`, `drift-report.json`, and `final|drafts/*.md` chapter files,
rejecting symlinks that escape the specback dir). It must **never** be exposed
over TCP/shared MCP gateways without adding authentication and path
containment. Stdin message lines are capped at 1 MiB and artifact files at
50 MiB; results are capped at 200 units per call to bound memory and LLM
context usage.

## Where the data comes from

Same as `build-search-index.py` (see the [specback-search skill](../skills/specback-search/SKILL.md)):

- `source-map.json`, `trace.json` — required (run specback Phase 0–3 first)
- `inventory.json`, `questions.json`, `drift-report.json` — optional
  (`questions` needs Phase 4, `drift` needs `detect-drift.py`)

## Tests

```bash
uv run pytest skills/specback-search/scripts/tests/test_specback_search_mcp.py -v
```

Covers protocol handling (initialize/tools/list/tools/call/ping), the 4
tools against fixture data, error semantics, and end-to-end stdio transport
(subprocess) including malformed-JSON recovery and clean EOF exit.
