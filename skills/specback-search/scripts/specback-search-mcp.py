#!/usr/bin/env python3
"""
specback-search-mcp.py — stdlib-only MCP stdio server wrapping build-search-index.py.

Implements the Model Context Protocol (MCP) over newline-delimited JSON-RPC 2.0
on stdin/stdout, exposing the specback-search CLI's query capabilities as four
tools: specback_search, specback_uncovered, specback_drift, specback_questions.

Stdlib only: json, sys, argparse, importlib.util, pathlib. No `mcp` SDK.

Protocol:
  - Each message is ONE JSON object per line on stdin; each response is ONE
    JSON object per line on stdout.
  - Never write anything but JSON-RPC responses to stdout (logs/errors -> stderr).
  - Exit 0 on EOF (stdin closed).

Usage:
    python3 specback-search-mcp.py [--version]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Load the wrapped CLI
# ---------------------------------------------------------------------------
# build-search-index.py has a hyphen in its filename, so it cannot be imported
# by name. Load it with importlib and register the module in sys.modules
# BEFORE exec_module so its `if __name__ == "__main__"` guard stays inactive.
_SCRIPT = Path(__file__).resolve().parent / "build-search-index.py"
_spec = importlib.util.spec_from_file_location("build_search_index", _SCRIPT)
assert _spec and _spec.loader
bsi = importlib.util.module_from_spec(_spec)
sys.modules["build_search_index"] = bsi  # register BEFORE exec
_spec.loader.exec_module(bsi)

SERVER_NAME = "specback-search"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_SPECBACK_DIR = ".specback"

# Input/output caps (H2 hardening): bound memory use and LLM-context flooding.
MAX_LINE_BYTES = 1024 * 1024       # 1 MiB per stdin JSON-RPC message line
MAX_QUERY_LEN = 500                # chars for 'query'
MAX_FILTER_LEN = 200               # chars for 'chapter' / 'role'
MAX_RESULTS = 200                  # source units returned per tool call

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def build_tools_list() -> list[dict[str, Any]]:
    """Return the four MCP tool definitions (name, description, inputSchema)."""
    return [
        {
            "name": "specback_search",
            "description": (
                "Search specback-generated data (source-map, trace, inventory) "
                "by source unit name or path, optionally filtered by confidence, "
                "chapter, or role"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to match against source unit name or path (optional when chapter/role/confidence is provided)",
                    },
                    "specback_dir": {
                        "type": "string",
                        "description": "Path to the .specback directory",
                        "default": DEFAULT_SPECBACK_DIR,
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["🟢", "🟡", "🔴"],
                        "description": "Filter by confidence level",
                    },
                    "chapter": {
                        "type": "string",
                        "description": "Filter by chapter slug (e.g. 03-data-model)",
                    },
                    "role": {
                        "type": "string",
                        "description": "Filter by source unit role (e.g. endpoint, model, module)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "Output format",
                        "default": "text",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "specback_uncovered",
            "description": (
                "List source units not covered by any spec chapter, optionally "
                "filtered by role or confidence"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "specback_dir": {
                        "type": "string",
                        "description": "Path to the .specback directory",
                        "default": DEFAULT_SPECBACK_DIR,
                    },
                    "role": {
                        "type": "string",
                        "description": "Filter by source unit role (e.g. endpoint, model, module)",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["🟢", "🟡", "🔴"],
                        "description": "Filter by confidence level",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "Output format",
                        "default": "text",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "specback_drift",
            "description": (
                "Show the latest drift report summary (affected spec sections, "
                "new uncovered sources, deleted references)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "specback_dir": {
                        "type": "string",
                        "description": "Path to the .specback directory",
                        "default": DEFAULT_SPECBACK_DIR,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "Output format",
                        "default": "text",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "specback_questions",
            "description": (
                "Show unresolved (or all) questions collected during spec generation"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "specback_dir": {
                        "type": "string",
                        "description": "Path to the .specback directory",
                        "default": DEFAULT_SPECBACK_DIR,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["open", "all"],
                        "description": "Question status filter",
                        "default": "open",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "Output format",
                        "default": "text",
                    },
                },
                "required": [],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Validation (crash-avoidance)
# ---------------------------------------------------------------------------


def _validate_specback_dir(specback_dir: str) -> tuple[bool, str]:
    """Validate the specback dir before touching bsi functions.

    bsi.load_json() raises SpecbackDataError when source-map.json or trace.json
    is missing, non-regular (FIFO/socket/device), oversized, or invalid JSON —
    an MCP server must NEVER die on a tool call, so every tool handler
    validates first. Returns (ok, error_message); when ok is False the
    caller must return an isError tool result instead of calling bsi functions.
    """
    d = Path(specback_dir)
    if not d.is_dir():
        return (
            False,
            f"specback-dir {specback_dir!r} is not a directory. "
            "Run specback first or pass a valid specback_dir.",
        )
    for required in ("source-map.json", "trace.json"):
        p = d / required
        if not p.exists():
            return False, f"Run specback first — missing {required}"
        if not p.is_file():
            return False, f"{required} is not a regular file"
        if p.stat().st_size > bsi.MAX_ARTIFACT_BYTES:
            return False, f"{required} exceeds {bsi.MAX_ARTIFACT_BYTES} bytes"
    return True, ""


def _intersect(current: list[Any], candidates: list[Any]) -> list[Any]:
    """Keep only candidates whose src_id is present in the current result set.

    Mirrors the CLI main() filter chaining: each additional filter intersects
    with the current results by src_id.
    """
    ids = {r.src_id for r in current}
    return [r for r in candidates if r.src_id in ids]


def _cap_results(results: list[Any]) -> list[Any]:
    """Cap the number of source units returned per tool call."""
    if len(results) <= MAX_RESULTS:
        return results
    return results[:MAX_RESULTS]


def _log_error(tool: str, exc: BaseException) -> None:
    """Log the full exception to stderr. stdout is reserved for the protocol."""
    print(f"[specback-search-mcp] {tool} failed: {exc!r}", file=sys.stderr)


def _validate_string_param(
    name: str, value: Any, *, max_len: int, allowed: tuple[str, ...] | None = None
) -> str | None:
    """Validate a string tool parameter; returns an error message or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return f"Invalid params: {name} must be a string"
    if len(value) > max_len:
        return f"Invalid params: {name} exceeds {max_len} chars"
    if allowed is not None and value not in allowed:
        return f"Invalid params: {name} must be one of {', '.join(allowed)}"
    return None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_search(arguments: dict[str, Any]) -> tuple[bool, str]:
    query = arguments.get("query", "")
    specback_dir = arguments.get("specback_dir", DEFAULT_SPECBACK_DIR)
    confidence = arguments.get("confidence")
    chapter = arguments.get("chapter")
    role = arguments.get("role")
    fmt = arguments.get("format", "text")

    if not isinstance(query, str):
        return True, "Invalid params: 'query' must be a string"
    if not query and not any(arguments.get(k) for k in ("chapter", "role", "confidence")):
        return True, "Invalid params: provide 'query' or at least one of chapter/role/confidence"
    if len(query) > MAX_QUERY_LEN:
        return True, f"Invalid params: query exceeds {MAX_QUERY_LEN} chars"
    for name, value in (("chapter", chapter), ("role", role)):
        err = _validate_string_param(name, value, max_len=MAX_FILTER_LEN)
        if err:
            return True, err
    err = _validate_string_param(
        "confidence", confidence, max_len=8, allowed=("🟢", "🟡", "🔴")
    )
    if err:
        return True, err
    if not isinstance(specback_dir, str) or not specback_dir:
        return True, "Invalid params: specback_dir must be a non-empty string"
    ok, err = _validate_specback_dir(specback_dir)
    if not ok:
        return True, err
    try:
        index = bsi.build_index(Path(specback_dir))
        results = bsi.search_by_name(index, query)
        if chapter:
            results = _intersect(results, bsi.filter_by_chapter(index, chapter))
        if role:
            results = _intersect(results, bsi.filter_by_role(index, role))
        if confidence:
            results = [r for r in results if r.confidence == confidence]
        truncated = len(results) > MAX_RESULTS
        results = _cap_results(results)
        title = f"「{query}」" if query else "All matching units"
        if truncated:
            title += f" (first {MAX_RESULTS} shown)"
        if fmt == "json":
            return False, bsi.format_results_json(results, None, None)
        return False, bsi.format_text_results(results, title)
    except bsi.SpecbackDataError as exc:
        return True, f"specback_search failed: {exc}"
    except Exception as exc:
        _log_error("specback_search", exc)
        return True, "specback_search failed: internal error"


def _tool_uncovered(arguments: dict[str, Any]) -> tuple[bool, str]:
    specback_dir = arguments.get("specback_dir", DEFAULT_SPECBACK_DIR)
    role = arguments.get("role")
    confidence = arguments.get("confidence")
    fmt = arguments.get("format", "text")

    for name, value in (("role", role),):
        err = _validate_string_param(name, value, max_len=MAX_FILTER_LEN)
        if err:
            return True, err
    err = _validate_string_param(
        "confidence", confidence, max_len=8, allowed=("🟢", "🟡", "🔴")
    )
    if err:
        return True, err
    if not isinstance(specback_dir, str) or not specback_dir:
        return True, "Invalid params: specback_dir must be a non-empty string"
    ok, err = _validate_specback_dir(specback_dir)
    if not ok:
        return True, err
    try:
        index = bsi.build_index(Path(specback_dir))
        results = bsi.find_uncovered(index)
        if role:
            results = _intersect(results, bsi.filter_by_role(index, role))
        if confidence:
            results = [r for r in results if r.confidence == confidence]
        truncated = len(results) > MAX_RESULTS
        results = _cap_results(results)
        title = "Uncovered"
        if truncated:
            title += f" (first {MAX_RESULTS} shown)"
        if fmt == "json":
            return False, bsi.format_results_json(results, None, None)
        return False, bsi.format_text_results(results, title)
    except bsi.SpecbackDataError as exc:
        return True, f"specback_uncovered failed: {exc}"
    except Exception as exc:
        _log_error("specback_uncovered", exc)
        return True, "specback_uncovered failed: internal error"


def _tool_drift(arguments: dict[str, Any]) -> tuple[bool, str]:
    specback_dir = arguments.get("specback_dir", DEFAULT_SPECBACK_DIR)
    fmt = arguments.get("format", "text")

    if not isinstance(specback_dir, str) or not specback_dir:
        return True, "Invalid params: specback_dir must be a non-empty string"
    ok, err = _validate_specback_dir(specback_dir)
    if not ok:
        return True, err
    try:
        index = bsi.build_index(Path(specback_dir))
        drift = bsi.get_drift_summary(index)
        if drift is None:
            if fmt == "json":
                return False, "{}"
            return False, "🔄 Drift Report: (none — run detect-drift.py first)"
        if fmt == "json":
            return False, bsi.format_results_json([], None, drift)
        return False, bsi.format_drift_text(drift)
    except bsi.SpecbackDataError as exc:
        return True, f"specback_drift failed: {exc}"
    except Exception as exc:
        _log_error("specback_drift", exc)
        return True, "specback_drift failed: internal error"


def _tool_questions(arguments: dict[str, Any]) -> tuple[bool, str]:
    specback_dir = arguments.get("specback_dir", DEFAULT_SPECBACK_DIR)
    mode = arguments.get("mode", "open")
    fmt = arguments.get("format", "text")

    err = _validate_string_param("mode", mode, max_len=8, allowed=("open", "all"))
    if err:
        return True, err
    if not isinstance(specback_dir, str) or not specback_dir:
        return True, "Invalid params: specback_dir must be a non-empty string"
    ok, err = _validate_specback_dir(specback_dir)
    if not ok:
        return True, err
    try:
        index = bsi.build_index(Path(specback_dir))
        questions = bsi.get_questions(index, mode)
        if fmt == "json":
            return False, bsi.format_results_json([], questions, None)
        return False, bsi.format_questions_text(questions, mode)
    except bsi.SpecbackDataError as exc:
        return True, f"specback_questions failed: {exc}"
    except Exception as exc:
        _log_error("specback_questions", exc)
        return True, "specback_questions failed: internal error"


def call_tool(name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    """Dispatch a tool call. Returns (is_error, text_output).

    Raises ValueError for unknown tool names.
    """
    if name == "specback_search":
        return _tool_search(arguments)
    if name == "specback_uncovered":
        return _tool_uncovered(arguments)
    if name == "specback_drift":
        return _tool_drift(arguments)
    if name == "specback_questions":
        return _tool_questions(arguments)
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# JSON-RPC message handling
# ---------------------------------------------------------------------------


def read_message(stream) -> dict[str, Any] | None:
    """Read one line from stream and parse it as JSON.

    Returns None on EOF; raises json.JSONDecodeError / ValueError /
    UnicodeDecodeError / RecursionError on a malformed or oversized line.
    Blank lines are skipped.
    """
    while True:
        line = stream.readline()
        if not line:
            return None
        if len(line) > MAX_LINE_BYTES:
            raise ValueError(f"message line exceeds {MAX_LINE_BYTES} bytes")
        line = line.strip()
        if not line:
            continue
        return json.loads(line)


def write_message(obj: dict[str, Any], stream) -> None:
    """Serialize one JSON-RPC message as a single newline-terminated JSON line."""
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request message (pure, no I/O).

    Returns the response dict, or None for notifications (no reply).
    """
    base: dict[str, Any] = {"jsonrpc": "2.0", "id": msg.get("id")}
    try:
        # JSON-RPC notifications carry no "id" and MUST NOT get a response
        # (e.g. notifications/initialized, notifications/cancelled, ...).
        if "id" not in msg:
            return None
        method = msg.get("method")

        if method == "initialize":
            params = msg.get("params")
            protocol = (
                params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
                if isinstance(params, dict)
                else DEFAULT_PROTOCOL_VERSION
            )
            if not isinstance(protocol, str):
                protocol = DEFAULT_PROTOCOL_VERSION
            return {
                **base,
                "result": {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "ping":
            return {**base, "result": {}}

        if method == "tools/list":
            return {**base, "result": {"tools": build_tools_list()}}

        if method == "tools/call":
            params = msg.get("params")
            if not isinstance(params, dict):
                return {**base, "error": {"code": -32602, "message": "Invalid params"}}
            name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(name, str) or not name:
                return {
                    **base,
                    "error": {"code": -32602, "message": "Invalid params: missing tool name"},
                }
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                is_error, text = call_tool(name, arguments)
            except ValueError as exc:
                return {
                    **base,
                    "error": {"code": -32602, "message": f"Invalid params: {exc}"},
                }
            except Exception as exc:
                _log_error("tools/call", exc)
                return {
                    **base,
                    "error": {"code": -32603, "message": "Internal error"},
                }
            return {
                **base,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                },
            }

        if method is None:
            # A message with an id but no method: a genuine client response
            # carries result/error keys — ignore it. Anything else is an
            # Invalid Request per JSON-RPC 2.0.
            if "result" in msg or "error" in msg:
                return None
            return {**base, "error": {"code": -32600, "message": "Invalid Request"}}

        return {**base, "error": {"code": -32601, "message": "Method not found"}}
    except Exception as exc:
        _log_error("handle_message", exc)
        return {**base, "error": {"code": -32603, "message": "Internal error"}}


# ---------------------------------------------------------------------------
# Stdio main loop
# ---------------------------------------------------------------------------


def run_stdio() -> int:
    """Main loop: read newline-delimited JSON-RPC from stdin, write responses to stdout.

    Never prints anything but JSON-RPC to stdout. Returns 0 on EOF.
    The read loop catches every client-craftable decode failure
    (JSONDecodeError, RecursionError from deep nesting, UnicodeDecodeError from
    invalid UTF-8) so the server survives malformed input.
    """
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    while True:
        try:
            msg = read_message(sys.stdin)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError):
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                sys.stdout,
            )
            continue
        if msg is None:
            return 0  # EOF — exit cleanly
        if not isinstance(msg, dict):
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                },
                sys.stdout,
            )
            continue
        response = handle_message(msg)
        if response is not None:
            try:
                write_message(response, sys.stdout)
            except BrokenPipeError:
                return 0  # client disconnected — normal teardown


def main(argv: list[str] | None = None) -> int:
    """Entry point. Only --version is accepted; otherwise serve on stdio."""
    parser = argparse.ArgumentParser(
        prog="specback-search-mcp",
        description="Stdlib-only MCP stdio server wrapping the specback-search CLI.",
    )
    parser.add_argument("--version", action="version", version="specback-search-mcp 0.1.0")
    parser.parse_args(argv)
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
