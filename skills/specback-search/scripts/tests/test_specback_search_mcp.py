"""Tests for specback-search-mcp.py (stdlib-only MCP stdio server).

Covers in-process handler/tool behavior (importlib-loaded module) and
subprocess transport behavior (Popen over stdin/stdout pipes).

Fixture JSON data is imported from the sibling test module
test_build_search_index.py; if that import fails, the fixtures are duplicated
below as a fallback.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "specback-search-mcp.py"

# ---------------------------------------------------------------------------
# Load the MCP server module in-process (register in sys.modules BEFORE exec)
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location("specback_search_mcp", SCRIPT)
assert _spec and _spec.loader
mcp = importlib.util.module_from_spec(_spec)
sys.modules["specback_search_mcp"] = mcp
_spec.loader.exec_module(mcp)

# ---------------------------------------------------------------------------
# Fixtures — try the sibling import first, duplicate on failure
# ---------------------------------------------------------------------------
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_build_search_index import (  # noqa: F401
        SOURCE_MAP,
        TRACE,
        INVENTORY,
        QUESTIONS,
        DRIFT_REPORT,
    )
except ImportError:  # pragma: no cover - fallback when sibling module unavailable
    SOURCE_MAP = {
        "schema_version": "0.2.0",
        "target_root": "myapp",
        "generated_at": "2026-08-01T12:00:00",
        "detected_frameworks": [],
        "warnings": [],
        "stats": {"files_scanned": 3, "units_total": 4},
        "units": [
            {
                "id": "SRC-0001", "path": "app/routes/users.py", "line_range": [10, 30],
                "language": "python", "role": "endpoint", "kind": "fastapi_endpoint",
                "tier": "middle", "name": "get_user",
                "signature": "async def get_user(user_id: int)",
                "fingerprint": "sha1:abc",
            },
            {
                "id": "SRC-0002", "path": "app/models/user.py", "line_range": [5, 80],
                "language": "python", "role": "model", "kind": "sqlalchemy_model",
                "tier": "middle", "name": "User",
                "signature": "class User(Base)",
                "fingerprint": "sha1:def",
            },
            {
                "id": "SRC-0003", "path": "app/models/product.py", "line_range": [1, 60],
                "language": "python", "role": "model", "kind": "sqlalchemy_model",
                "tier": "middle", "name": "Product",
                "signature": "class Product(Base)",
                "fingerprint": "sha1:ghi",
            },
            {
                "id": "SRC-0004", "path": "app/routes/auth.py", "line_range": [5, 45],
                "language": "python", "role": "endpoint", "kind": "fastapi_endpoint",
                "tier": "middle", "name": "login",
                "signature": "async def login(request: Request)",
                "fingerprint": "sha1:jkl",
            },
        ],
    }

    TRACE = {
        "schema_version": "0.2.0",
        "generated_at": "2026-08-01T12:00:00",
        "source_units_total": 4,
        "source_units_covered": 3,
        "source_units_excluded": 0,
        "source_units_uncovered": 1,
        "mece_passed": False,
        "by_source": {
            "SRC-0001": {
                "path": "app/routes/users.py", "line_range": [10, 30],
                "covered_by_sections": [{"file": "02-feature-specs.md", "section": "2.1 API endpoints"}],
                "excluded": False, "excluded_reason": None,
            },
            "SRC-0002": {
                "path": "app/models/user.py", "line_range": [5, 80],
                "covered_by_sections": [{"file": "03-data-model.md", "section": "3.1 Entities"}],
                "excluded": False, "excluded_reason": None,
            },
            "SRC-0003": {
                "path": "app/models/product.py", "line_range": [1, 60],
                "covered_by_sections": [{"file": "03-data-model.md", "section": "3.1 Entities"}],
                "excluded": False, "excluded_reason": None,
            },
            "SRC-0004": {
                "path": "app/routes/auth.py", "line_range": [5, 45],
                "covered_by_sections": [],
                "excluded": False, "excluded_reason": None,
            },
        },
        "by_section": {
            "02-feature-specs.md::2.1 API endpoints": ["SRC-0001"],
            "03-data-model.md::3.1 Entities": ["SRC-0002", "SRC-0003"],
        },
        "uncovered_units": ["SRC-0004"],
    }

    INVENTORY = {
        "units": [
            {
                "id": "INV-001", "type": "endpoint", "name": "User API",
                "file": "app/routes/users.py", "line": 10,
                "covered_by": ["Feature specs"], "related_source_ids": ["SRC-0001"],
            },
            {
                "id": "INV-002", "type": "orm_model", "name": "User model",
                "file": "app/models/user.py", "line": 5,
                "covered_by": ["Data Model"], "related_source_ids": ["SRC-0002"],
            },
            {
                "id": "INV-003", "type": "orm_model", "name": "Product model",
                "file": "app/models/product.py", "line": 1,
                "covered_by": ["Data Model"], "related_source_ids": ["SRC-0003"],
            },
        ],
    }

    QUESTIONS = [
        {
            "id": "Q-001", "generated_at_phase": "investigation",
            "category": "business_rule", "body": "注文のキャンセル期限は？",
            "severity": "critical", "resolution_type": "ask_sme",
            "status": "open",
        },
        {
            "id": "Q-002", "generated_at_phase": "verification",
            "category": "architecture_decision", "body": "なぜSQLiteなのか？",
            "severity": "nice-to-have", "resolution_type": "agent_inference",
            "status": "answered", "answer": "開発環境の簡素化のため",
            "answerer": "agent_inference",
        },
    ]

    DRIFT_REPORT = {
        "schema_version": "0.1.0",
        "generated_at": "2026-08-01T14:00:00",
        "base": "main",
        "summary": {
            "changed_files": 3,
            "affected_spec_sections": 2,
            "new_uncovered_sources": 0,
            "deleted_sources_with_refs": 0,
            "no_impact_changes": 1,
        },
        "changes": [],
        "deleted_with_refs": [],
        "new_uncovered": [],
        "no_impact": [],
    }


@pytest.fixture
def specback_dir(tmp_path: Path) -> Path:
    """Create a temporary .specback directory with fixture JSON files."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    (sb / "inventory.json").write_text(json.dumps(INVENTORY), encoding="utf-8")
    (sb / "questions.json").write_text(json.dumps(QUESTIONS), encoding="utf-8")
    (sb / "drift-report.json").write_text(json.dumps(DRIFT_REPORT), encoding="utf-8")
    return sb


def _write_confidence_chapters(specback_dir: Path) -> None:
    """Add final/ chapter files with REF annotations so confidence is extractable."""
    final = specback_dir / "final"
    final.mkdir()
    (final / "03-data-model.md").write_text(
        "<!-- REF: SRC-0002 --> 🟢\n<!-- REF: SRC-0003 --> 🟡\n",
        encoding="utf-8",
    )


def _spawn() -> subprocess.Popen:
    """Spawn the MCP server as a subprocess with piped stdio."""
    return subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cleanup_proc(proc: subprocess.Popen) -> None:
    """Close all three PIPE streams and reap the server subprocess.

    Closing stdin signals EOF (triggering a clean server exit), then stdout
    and stderr are closed too so no pipe file object is left open. Without
    closing stdout/stderr the suite emits ResourceWarnings and leaks file
    descriptors under -W error::ResourceWarning.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    assert proc.wait(timeout=15) == 0


# ═══════════════════════════════════════════════════════════════════════════
# handle_message: protocol level
# ═══════════════════════════════════════════════════════════════════════════


def test_initialize_returns_server_info():
    resp = mcp.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}}
    )
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["serverInfo"]["name"] == "specback-search"
    assert result["serverInfo"]["version"] == "0.1.0"
    assert result["capabilities"]["tools"]["listChanged"] is False


def test_initialize_echoes_protocol_version():
    resp = mcp.handle_message(
        {"jsonrpc": "2.0", "id": "abc", "method": "initialize",
         "params": {"protocolVersion": "2025-03-26"}}
    )
    assert resp["result"]["protocolVersion"] == "2025-03-26"
    assert resp["id"] == "abc"


def test_initialize_default_protocol_version():
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_notification_returns_none():
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping_returns_empty_result():
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert resp["result"] == {}
    assert resp["id"] == 3


def test_tools_list_has_4_tools():
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == ["specback_search", "specback_uncovered", "specback_drift", "specback_questions"]


def test_tools_list_schemas_have_required_fields():
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    tools = resp["result"]["tools"]
    for tool in tools:
        assert tool["name"]
        assert tool["description"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert isinstance(schema["properties"], dict)
    by_name = {t["name"]: t["inputSchema"] for t in tools}
    # query is optional when chapter/role/confidence is provided (CLI parity)
    assert "query" in by_name["specback_search"]["properties"]
    assert "query" not in by_name["specback_search"]["required"]
    assert by_name["specback_search"]["properties"]["specback_dir"]["default"] == ".specback"
    assert by_name["specback_search"]["properties"]["format"]["enum"] == ["text", "json"]
    assert by_name["specback_search"]["properties"]["confidence"]["enum"] == ["🟢", "🟡", "🔴"]
    assert by_name["specback_uncovered"]["properties"]["specback_dir"]["default"] == ".specback"
    assert by_name["specback_drift"]["properties"]["specback_dir"]["default"] == ".specback"
    assert by_name["specback_questions"]["properties"]["mode"]["enum"] == ["open", "all"]
    assert by_name["specback_questions"]["properties"]["mode"]["default"] == "open"


def test_unknown_method_error_32601():
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 7, "method": "bogus/method"})
    assert resp["error"]["code"] == -32601
    assert resp["error"]["message"] == "Method not found"
    assert resp["id"] == 7


def test_call_unknown_tool_error():
    resp = mcp.handle_message(
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}}
    )
    assert resp["error"]["code"] == -32602
    assert "nope" in resp["error"]["message"]


def test_call_tool_unknown_raises_value_error():
    with pytest.raises(ValueError):
        mcp.call_tool("nope", {})


def test_call_invalid_params_missing_name():
    resp = mcp.handle_message(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"arguments": {}}}
    )
    assert resp["error"]["code"] == -32602


# ═══════════════════════════════════════════════════════════════════════════
# call_tool: specback_search
# ═══════════════════════════════════════════════════════════════════════════


def test_call_specback_search_text(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": str(specback_dir)}
    )
    assert is_error is False
    assert "SRC-0002" in text
    assert "app/models/user.py" in text


def test_call_specback_search_json(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_search",
        {"query": "User", "specback_dir": str(specback_dir), "format": "json"},
    )
    assert is_error is False
    data = json.loads(text)
    assert data["results"]
    assert data["results"][0]["src_id"].startswith("SRC-")
    assert data["results"][0]["path"]
    assert {r["src_id"] for r in data["results"]} == {"SRC-0001", "SRC-0002"}


def test_call_specback_search_no_match(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "ZZZZNOTFOUND", "specback_dir": str(specback_dir)}
    )
    assert is_error is False
    assert "no results found" in text


def test_call_specback_search_with_chapter_filter(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_search",
        {"query": "User", "specback_dir": str(specback_dir), "chapter": "03-data-model"},
    )
    assert is_error is False
    assert "SRC-0002" in text  # covered by 03-data-model
    assert "SRC-0001" not in text  # covered by 02-feature-specs


def test_call_specback_search_with_role_filter(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_search",
        {"query": "User", "specback_dir": str(specback_dir), "role": "model"},
    )
    assert is_error is False
    assert "SRC-0002" in text
    assert "SRC-0001" not in text  # endpoint role


def test_call_specback_search_with_confidence_filter(specback_dir: Path):
    _write_confidence_chapters(specback_dir)
    is_error, text = mcp.call_tool(
        "specback_search",
        {"query": "Product", "specback_dir": str(specback_dir), "confidence": "🟡"},
    )
    assert is_error is False
    assert "SRC-0003" in text  # 🟡 per chapter REF
    assert "SRC-0002" not in text  # 🟢 per chapter REF


def test_call_specback_search_missing_query():
    is_error, text = mcp.call_tool("specback_search", {"specback_dir": "."})
    assert is_error is True
    assert "query" in text


# ═══════════════════════════════════════════════════════════════════════════
# call_tool: specback_uncovered
# ═══════════════════════════════════════════════════════════════════════════


def test_call_specback_uncovered(specback_dir: Path):
    is_error, text = mcp.call_tool("specback_uncovered", {"specback_dir": str(specback_dir)})
    assert is_error is False
    assert "SRC-0004" in text
    assert "app/routes/auth.py" in text
    assert "SRC-0001" not in text


def test_call_specback_uncovered_with_role_filter(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_uncovered",
        {"specback_dir": str(specback_dir), "role": "endpoint"},
    )
    assert is_error is False
    assert "SRC-0004" in text


# ═══════════════════════════════════════════════════════════════════════════
# call_tool: specback_drift
# ═══════════════════════════════════════════════════════════════════════════


def test_call_specback_drift(specback_dir: Path):
    is_error, text = mcp.call_tool("specback_drift", {"specback_dir": str(specback_dir)})
    assert is_error is False
    assert "Changed files" in text
    assert "3" in text


def test_call_specback_drift_missing(tmp_path: Path):
    """No drift-report.json -> not an error, text mentions 'none'."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    is_error, text = mcp.call_tool("specback_drift", {"specback_dir": str(sb)})
    assert is_error is False
    assert "none" in text


def test_call_specback_drift_json(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_drift", {"specback_dir": str(specback_dir), "format": "json"}
    )
    assert is_error is False
    data = json.loads(text)
    assert data["drift"]["summary"]["changed_files"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# call_tool: specback_questions
# ═══════════════════════════════════════════════════════════════════════════


def test_call_specback_questions_open(specback_dir: Path):
    is_error, text = mcp.call_tool("specback_questions", {"specback_dir": str(specback_dir)})
    assert is_error is False
    assert "Q-001" in text
    assert "Q-002" not in text  # answered


def test_call_specback_questions_all(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_questions", {"specback_dir": str(specback_dir), "mode": "all"}
    )
    assert is_error is False
    assert "Q-001" in text
    assert "Q-002" in text


def test_call_specback_questions_json(specback_dir: Path):
    is_error, text = mcp.call_tool(
        "specback_questions",
        {"specback_dir": str(specback_dir), "mode": "all", "format": "json"},
    )
    assert is_error is False
    data = json.loads(text)
    assert [q["id"] for q in data["questions"]] == ["Q-001", "Q-002"]


# ═══════════════════════════════════════════════════════════════════════════
# call_tool: error handling (server must never crash)
# ═══════════════════════════════════════════════════════════════════════════


def test_call_missing_specback_dir():
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": "/nonexistent/xyz"}
    )
    assert is_error is True
    assert "directory" in text


def test_call_missing_source_map(tmp_path: Path):
    """specback dir exists but source-map.json is missing -> isError, no crash."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": str(sb)}
    )
    assert is_error is True
    assert "source-map.json" in text


def test_call_missing_trace(tmp_path: Path):
    """specback dir exists but trace.json is missing -> isError, no crash."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    is_error, text = mcp.call_tool(
        "specback_uncovered", {"specback_dir": str(sb)}
    )
    assert is_error is True
    assert "trace.json" in text


# ═══════════════════════════════════════════════════════════════════════════
# read_message / write_message unit tests
# ═══════════════════════════════════════════════════════════════════════════


def test_read_message_parses_line():
    stream = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    msg = mcp.read_message(stream)
    assert msg == {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_read_message_returns_none_on_eof():
    stream = io.StringIO("")
    assert mcp.read_message(stream) is None


def test_read_message_raises_on_bad_json():
    stream = io.StringIO("not json\n")
    with pytest.raises(json.JSONDecodeError):
        mcp.read_message(stream)


def test_read_message_skips_blank_lines():
    stream = io.StringIO("\n\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\n")
    msg = mcp.read_message(stream)
    assert msg["method"] == "ping"


def test_write_message_writes_json_line():
    stream = io.StringIO()
    mcp.write_message({"jsonrpc": "2.0", "id": 1, "result": {}}, stream)
    out = stream.getvalue()
    assert out.endswith("\n")
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 1, "result": {}}


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def test_version_flag():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--version"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "specback-search-mcp 0.1.0" in result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# Transport tests (subprocess over stdin/stdout)
# ═══════════════════════════════════════════════════════════════════════════


def test_transport_initialize_and_list():
    proc = _spawn()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"]["serverInfo"]["name"] == "specback-search"
        assert resp["result"]["protocolVersion"] == "2024-11-05"

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["specback_search", "specback_uncovered", "specback_drift", "specback_questions"]
    finally:
        _cleanup_proc(proc)


def test_transport_call_tool_end_to_end(specback_dir: Path):
    proc = _spawn()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        )
        proc.stdin.flush()
        json.loads(proc.stdout.readline())  # consume initialize response

        proc.stdin.write(
            json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "specback_search",
                           "arguments": {"query": "User", "specback_dir": str(specback_dir)}},
            }) + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"]["isError"] is False
        text = resp["result"]["content"][0]["text"]
        assert "SRC-0002" in text
        assert "app/models/user.py" in text
    finally:
        _cleanup_proc(proc)


def test_transport_malformed_json():
    proc = _spawn()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write("not json\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["error"]["code"] == -32700
        assert resp["id"] is None

        # Server is still alive: a subsequent initialize must succeed.
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"]["serverInfo"]["name"] == "specback-search"
    finally:
        _cleanup_proc(proc)

# ═══════════════════════════════════════════════════════════════════════════
# Regression tests — agency review findings (deleg_3bf8da25, 2026-08-11)
# Core invariant: the server must NEVER die on a tool call or a malformed
# stdin message. Each crash vector from the review gets a regression test.
# ═══════════════════════════════════════════════════════════════════════════


def test_corrupt_artifact_is_error_not_crash(tmp_path: Path):
    """F1: invalid JSON in source-map.json must return isError, not kill the server."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text("{ NOT VALID JSON", encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": str(sb)}
    )
    assert is_error is True
    assert "invalid JSON" in text


def test_corrupt_artifact_transport_survives(tmp_path: Path):
    """F1 transport: server stays alive after a corrupt artifact error."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text("{ NOT VALID JSON", encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    proc = _spawn()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"}}) + "\n"
        )
        proc.stdin.flush()
        json.loads(proc.stdout.readline())

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "specback_search",
                                   "arguments": {"query": "User", "specback_dir": str(sb)}}}) + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"]["isError"] is True

        # Server is still alive.
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"] == {}
    finally:
        _cleanup_proc(proc)


def test_transport_invalid_utf8_survives():
    """F3/B2: invalid UTF-8 bytes on stdin must not kill the server."""
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(b"\xff\xfe\x00\x01\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        resp = json.loads(line.decode("utf-8"))
        assert resp["error"]["code"] == -32700
        assert resp["id"] is None

        # Server is still alive.
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline().decode("utf-8"))
        assert resp["result"] == {}
    finally:
        _cleanup_proc(proc)


def test_transport_deep_nesting_survives():
    """F2: deeply-nested JSON (RecursionError) must not kill the server."""
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(b"[" * 100000 + b"]" * 100000 + b"\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        resp = json.loads(line.decode("utf-8"))
        assert resp["error"]["code"] == -32700

        # Server is still alive.
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline().decode("utf-8"))
        assert resp["result"] == {}
    finally:
        _cleanup_proc(proc)


def test_fifo_artifact_rejected(tmp_path: Path):
    """F4: FIFO/special file as source-map.json must be rejected, not hang."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    os.mkfifo(sb / "source-map.json")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": str(sb)}
    )
    assert is_error is True
    assert "not a regular file" in text


def test_notification_gets_no_response():
    """S2: any id-less message is a notification and MUST NOT get a reply."""
    for method in ("notifications/initialized", "notifications/cancelled", "notifications/roots/list_changed"):
        resp = mcp.handle_message({"jsonrpc": "2.0", "method": method})
        assert resp is None


def test_id_without_method_invalid_request():
    """S3: message with id but no method (and no result/error) → -32600."""
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 99, "params": {}})
    assert resp is not None
    assert resp["error"]["code"] == -32600


def test_client_response_ignored():
    """S3: a genuine client response (result/error keys, no method) is ignored."""
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert resp is None


def test_null_specback_dir_clean_error():
    """S4: non-string specback_dir must yield a clean isError, not -32603."""
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "User", "specback_dir": None}
    )
    assert is_error is True
    assert "specback_dir must be a non-empty string" in text


def test_drift_json_missing_report_parseable(specback_dir: Path):
    """S1: specback_drift with format=json and no drift report → parseable JSON."""
    (specback_dir / "drift-report.json").unlink()
    is_error, text = mcp.call_tool(
        "specback_drift", {"specback_dir": str(specback_dir), "format": "json"}
    )
    assert is_error is False
    assert json.loads(text) == {}


def test_filter_only_query_no_query_string(specback_dir: Path):
    """S6: chapter filter without query matches all units in that chapter."""
    is_error, text = mcp.call_tool(
        "specback_search", {"chapter": "03-data-model", "specback_dir": str(specback_dir)}
    )
    assert is_error is False
    assert "SRC-0002" in text
    assert "SRC-0003" in text


def test_search_no_query_no_filter_rejected():
    """S6: query and all filters absent → clean isError."""
    is_error, text = mcp.call_tool("specback_search", {})
    assert is_error is True
    assert "query" in text or "chapter" in text


def test_query_too_long_rejected(specback_dir: Path):
    """H2: oversized query must be rejected before touching data."""
    is_error, text = mcp.call_tool(
        "specback_search", {"query": "x" * 501, "specback_dir": str(specback_dir)}
    )
    assert is_error is True
    assert "exceeds" in text


def test_transport_stdout_purity(specback_dir: Path):
    """Security N1: every stdout line must be a JSON-RPC message, nothing else."""
    proc = _spawn()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "specback_search",
                        "arguments": {"query": "User", "specback_dir": str(specback_dir)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        for m in msgs:
            proc.stdin.write(json.dumps(m) + "\n")
        proc.stdin.flush()
        for _ in msgs:
            line = proc.stdout.readline()
            assert line.strip(), "stdout line must not be empty"
            resp = json.loads(line)
            assert resp["jsonrpc"] == "2.0"
    finally:
        _cleanup_proc(proc)
