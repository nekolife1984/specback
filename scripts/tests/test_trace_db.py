#!/usr/bin/env python3
"""Tests for trace_db.py — SQLite trace DB for specback session state."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

# Ensure the parent directory is on sys.path for imports
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from trace_db import TraceDB, _now_iso, _new_id  # noqa: E402
import trace_db  # noqa: E402


# ===================================================================
# Fixtures
# ===================================================================


def test_now_iso_z_format():
    """_now_iso returns a UTC timestamp in YYYY-MM-DDTHH:MM:SSZ format."""
    from datetime import datetime
    s = _now_iso()
    parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed is not None
    assert s.endswith("Z")


def test_new_id_length():
    assert len(_new_id()) == 8
    assert len(_new_id(16)) == 16


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    """Return a temporary database path that will be cleaned up."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="trace_test_")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def db(tmp_db_path: str) -> TraceDB:
    """Create a fresh TraceDB instance on a temporary database."""
    return TraceDB(tmp_db_path)


@pytest.fixture
def db_with_session(tmp_db_path: str) -> TraceDB:
    """Create a TraceDB with a basic session."""
    db = TraceDB(tmp_db_path)
    db.session_start("test_001", engineer="test_bot", adw_name="test_run",
                      request="Integration test")
    return db


@pytest.fixture
def db_with_session_and_phase(tmp_db_path: str) -> TraceDB:
    """Create a TraceDB with a session and one phase."""
    db = TraceDB(tmp_db_path)
    db.session_start("test_001")
    db.phase_start("test_001", "ph_01_recon", seq=1, name="recon",
                    kind="agent", owner="scout", description="Initial recon")
    return db


@pytest.fixture
def populated_db(tmp_db_path: str) -> TraceDB:
    """Return a TraceDB pre-populated with a session, phases, events, etc."""
    db = TraceDB(tmp_db_path)
    db.session_start("test_001", engineer="test_bot", adw_name="test_run",
                      request="Integration test")

    db.phase_start("test_001", "ph_01_recon", seq=1, name="recon",
                   kind="agent", owner="scout")
    db.phase_start("test_001", "ph_02_wbs", seq=2, name="wbs", kind="code")
    db.phase_start("test_001", "ph_03_investigate", seq=3, name="investigate",
                   kind="agent", owner="investigator")

    db.phase_finish("ph_01_recon", ok=True)
    db.phase_finish("ph_02_wbs", ok=True)

    db.event("test_001", "ph_01_recon", type="log", name="scout_found",
             payload={"files": 42})
    db.event("test_001", "ph_02_wbs", type="log", name="wbs_done",
             payload={"chapters": 8})

    db.envelope_save("env_001", "test_001", "ph_01_recon", agent="scout",
                     output_type="ReconOutput",
                     payload={"files": 42, "template": "library-sdk"})

    db.gate_result("test_001", "ph_02_wbs", "artifacts_exist", passed=True,
                   checks=[{"item": "wbs.json", "ok": True, "note": "exists"}])

    db.spec_unit_save("ch_01", kind="chapter", status="draft",
                       confidence="ASSUMED", label="Overview")
    db.spec_unit_save("ch_02", kind="chapter", status="verified",
                       confidence="VERIFIED", label="Architecture")

    db.chapter_draft_save("ch_01", "ph_03_investigate", content_hash="abc123",
                           ref_count=5, total_lines=120, confidence_ratio=0.8,
                           mermaid_count=2)

    db.question_save("Q-001", "ph_03_investigate", "What DB do we use?",
                      severity="critical", status="resolved",
                      resolution="PostgreSQL",
                      category="architecture_decision")

    db.session_finish("test_001", ok=True)
    return db


# ===================================================================
# Schema & Initialisation
# ===================================================================


class TestInit:
    def test_creates_all_tables(self, db: TraceDB):
        """All expected tables should exist after init."""
        tables = {
            r[0] for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "sessions", "phases", "events", "envelopes",
            "gate_results", "agent_sessions", "processes",
            "spec_units", "chapter_drafts", "questions_bank",
        }
        missing = expected - tables
        assert not missing, f"Missing tables: {missing}"

    def test_wal_mode(self, db: TraceDB):
        """WAL mode should be enabled."""
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_multiple_dbs_are_independent(self, tmp_db_path: str):
        """Two TraceDB instances on different paths must not interfere."""
        path2 = tmp_db_path + "_other"
        try:
            db1 = TraceDB(tmp_db_path)
            db2 = TraceDB(path2)
            db1.session_start("s1")
            db2.session_start("s2")
            assert db1.get_session("s1") is not None
            assert db1.get_session("s2") is None
            assert db2.get_session("s2") is not None
            assert db2.get_session("s1") is None
            db1.close()
            db2.close()
        finally:
            for p in [path2]:
                if os.path.exists(p):
                    os.remove(p)

    def test_migration_additive(self, db: TraceDB):
        """Additive migrations should not fail on a fresh DB."""
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sessions)")}
        assert "archived" in cols


# ===================================================================
# Sessions
# ===================================================================


class TestSessions:
    def test_session_start(self, db: TraceDB):
        db.session_start("s_abc", engineer="bot", adw_name="test",
                          request="Hello")
        s = db.get_session("s_abc")
        assert s is not None
        assert s["adw_id"] == "s_abc"
        assert s["status"] == "running"
        assert s["adw_name"] == "test"
        assert s["request"] == "Hello"
        assert s["started_at"] is not None

    def test_session_finish(self, db: TraceDB):
        db.session_start("s_xyz")
        db.session_finish("s_xyz", ok=True)
        s = db.get_session("s_xyz")
        assert s is not None
        assert s["status"] == "success"
        assert s["ended_at"] is not None

    def test_session_finish_fail(self, db: TraceDB):
        db.session_start("s_fail")
        db.session_finish("s_fail", ok=False)
        s = db.get_session("s_fail")
        assert s is not None
        assert s["status"] == "fail"

    def test_session_add_usage(self, db: TraceDB):
        db.session_start("s_usage")
        db.session_add_usage("s_usage", 500, 0.01)
        db.session_add_usage("s_usage", 300, 0.007)
        s = db.get_session("s_usage")
        assert s is not None
        assert s["total_tokens"] == 800
        assert abs(s["total_cost"] - 0.017) < 0.001

    def test_session_set_request(self, db: TraceDB):
        db.session_start("s_req")
        db.session_set_request("s_req", "Updated request")
        s = db.get_session("s_req")
        assert s is not None
        assert s["request"] == "Updated request"

    def test_get_session_not_found(self, db: TraceDB):
        assert db.get_session("nonexistent") is None

    def test_list_sessions(self, populated_db: TraceDB):
        sessions = populated_db.list_sessions(limit=10)
        assert len(sessions) >= 1
        assert sessions[0]["adw_id"] == "test_001"


# ===================================================================
# Phases
# ===================================================================


class TestPhases:
    def test_phase_start(self, db_with_session_and_phase: TraceDB):
        p = db_with_session_and_phase.get_phase("ph_01_recon")
        assert p is not None
        assert p["adw_id"] == "test_001"
        assert p["name"] == "recon"
        assert p["kind"] == "agent"
        assert p["owner"] == "scout"
        assert p["description"] == "Initial recon"
        assert p["status"] == "running"

    def test_phase_finish(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.phase_finish("ph_01_recon", ok=True)
        p = db.get_phase("ph_01_recon")
        assert p is not None
        assert p["status"] == "success"
        assert p["ended_at"] is not None

    def test_phase_finish_error(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.phase_finish("ph_01_recon", ok=False, error="Timeout")
        p = db.get_phase("ph_01_recon")
        assert p is not None
        # ok=False + error string → status becomes "error"
        assert p["status"] == "error"
        assert "Timeout" in (p.get("error") or "")

    def test_list_phases(self, populated_db: TraceDB):
        phases = populated_db.list_phases("test_001")
        assert len(phases) == 3
        names = [p["name"] for p in phases]
        assert names == ["recon", "wbs", "investigate"]

    def test_get_phase_not_found(self, db: TraceDB):
        assert db.get_phase("nonexistent") is None

    def test_phase_creates_events(self, db_with_session: TraceDB):
        db = db_with_session
        db.phase_start("test_001", "ph_evt", seq=1, name="test", kind="code")
        events = db.list_events(adw_id="test_001")
        types = [e["type"] for e in events]
        assert "phase_start" in types


# ===================================================================
# Events
# ===================================================================


class TestEvents:
    def test_event_created_with_id(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        eid = db.event("test_001", "ph_01_recon", type="log", name="custom",
                        payload={"key": "val"})
        assert eid.startswith("evt_")

    def test_list_events_by_session(self, db_with_session: TraceDB):
        db = db_with_session
        db.event("test_001", type="info", name="msg1")
        db.event("test_001", type="info", name="msg2")
        evts = db.list_events(adw_id="test_001", limit=10)
        assert len(evts) == 2

    def test_list_events_all(self, populated_db: TraceDB):
        evts = populated_db.list_events(limit=20)
        assert len(evts) >= 2


# ===================================================================
# Envelopes
# ===================================================================


class TestEnvelopes:
    def test_envelope_save(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.envelope_save("env_01", "test_001", phase_id="ph_01_recon",
                          agent="scout", output_type="ReconOutput",
                          payload={"files": 10}, valid=True)
        envs = db.list_envelopes("test_001")
        assert len(envs) == 1
        assert envs[0]["output_type"] == "ReconOutput"

    def test_list_envelopes_empty(self, db: TraceDB):
        assert db.list_envelopes("nonexistent") == []


# ===================================================================
# Gate results
# ===================================================================


class TestGateResults:
    def test_gate_result_passed(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.gate_result("test_001", "ph_01_recon", "coverage_check", passed=True,
                        checks=[{"item": "lines", "ok": True, "note": "ok"}])
        gates = db.list_gate_results("test_001")
        assert len(gates) == 1
        assert gates[0]["passed"] == 1

    def test_gate_result_failed(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.gate_result("test_001", "ph_01_recon", "ref_check", passed=False,
                        violations=["Missing REFs"])
        gates = db.list_gate_results("test_001")
        assert len(gates) == 1
        assert gates[0]["passed"] == 0
        violations = json.loads(gates[0]["violations_json"])
        assert "Missing REFs" in violations


# ===================================================================
# Specback extensions
# ===================================================================


class TestSpecUnits:
    def test_spec_unit_create(self, db: TraceDB):
        db.spec_unit_save("ch_01", kind="chapter", status="draft",
                           confidence="ASSUMED", label="Overview")
        db.spec_unit_save("su_01", kind="source_unit", status="verified",
                           confidence="VERIFIED")

    def test_spec_unit_update(self, db: TraceDB):
        db.spec_unit_save("ch_01", kind="chapter", status="draft",
                           confidence="ASSUMED")
        db.spec_unit_save("ch_01", kind="chapter", status="verified",
                           confidence="VERIFIED")
        row = db.conn.execute(
            "SELECT status, confidence FROM spec_units WHERE id='ch_01'"
        ).fetchone()
        assert row[0] == "verified"
        assert row[1] == "VERIFIED"


class TestChapterDrafts:
    def test_chapter_draft_save(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.chapter_draft_save("ch_01", "ph_01_recon", content_hash="abc123",
                               ref_count=5, total_lines=120,
                               confidence_ratio=0.85, mermaid_count=2)

    def test_chapter_draft_update(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.chapter_draft_save("ch_01", "ph_01_recon", ref_count=5)
        db.chapter_draft_save("ch_01", "ph_01_recon", ref_count=8)
        row = db.conn.execute(
            "SELECT ref_count FROM chapter_drafts WHERE id='ch_01'"
        ).fetchone()
        assert row[0] == 8


class TestQuestions:
    def test_question_save(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.question_save("Q-001", "ph_01_recon", "What DB?",
                          severity="critical", status="pending")

    def test_question_resolved(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.question_save("Q-002", "ph_01_recon", "What framework?",
                          severity="important", status="resolved",
                          resolution="FastAPI", category="architecture_decision")
        qs = db.list_questions(status="resolved")
        assert len(qs) == 1
        assert qs[0]["resolution"] == "FastAPI"

    def test_list_questions_filtered(self, db_with_session_and_phase: TraceDB):
        db = db_with_session_and_phase
        db.question_save("Q-003", "ph_01_recon", "Q3", severity="nice_to_have",
                          status="pending")
        db.question_save("Q-004", "ph_01_recon", "Q4", severity="critical",
                          status="resolved", resolution="Yes")
        pending = db.list_questions(status="pending")
        resolved = db.list_questions(status="resolved")
        assert len(pending) == 1
        assert len(resolved) == 1


# ===================================================================
# Bidirectional sync
# ===================================================================


class TestStateSync:
    def test_export_to_state_json(self, populated_db: TraceDB, tmp_db_path: str):
        """Export should produce a valid JSON with expected fields."""
        state_path = tmp_db_path + "_state.json"
        try:
            state = populated_db.export_to_state_json(state_path)
            assert state["current_phase"] == 3
            assert "phase_progress" in state
            assert state["phase_progress"]["phase_1"]["completed_subtasks"] == 1
            assert state["phase_progress"]["phase_2"]["completed_subtasks"] == 1
            assert state["phase_progress"]["phase_3"]["completed_subtasks"] == 0
            assert state["all_quality_gates_passed"] is False
            assert state["adw_id"] == "test_001"
            assert len(state["session_history"]) >= 1
            assert os.path.exists(state_path)
            with open(state_path) as f:
                disk_state = json.load(f)
            assert disk_state["adw_id"] == "test_001"
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)

    def test_export_with_questions_and_chapters(self, populated_db: TraceDB,
                                                 tmp_db_path: str):
        """Export with specback-specific data."""
        state_path = tmp_db_path + "_state2.json"
        try:
            state = populated_db.export_to_state_json(state_path)
            assert "questions" in state
            assert state["questions"][0]["id"] == "Q-001"
            assert "chapter_drafts" in state
            assert state["chapter_drafts"]["ch_01"]["ref_count"] == 5
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)

    def test_export_empty(self, db: TraceDB, tmp_db_path: str):
        """Export on an empty DB should return an empty dict."""
        state_path = tmp_db_path + "_empty.json"
        try:
            state = db.export_to_state_json(state_path)
            assert state == {}
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)

    def test_export_state_json_symlink_not_followed(self, populated_db: TraceDB,
                                                    tmp_db_path: str):
        """atomic_write_json replaces the symlink itself, never the target."""
        state_path = tmp_db_path + "_symlink_state.json"
        victim = tmp_db_path + "_victim.json"
        with open(victim, "w") as f:
            f.write("secret")
        try:
            os.symlink(victim, state_path)
            state = populated_db.export_to_state_json(state_path)
            assert state["adw_id"] == "test_001"
            with open(victim) as f:
                assert f.read() == "secret"
            assert not os.path.islink(state_path)
            with open(state_path) as f:
                assert json.load(f)["adw_id"] == "test_001"
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)
            if os.path.exists(victim):
                os.remove(victim)

    def test_import_from_state_json(self, populated_db: TraceDB,
                                     tmp_db_path: str):
        """Import should reconstruct session data from state.json."""
        state_path = tmp_db_path + "_import.json"
        try:
            state = populated_db.export_to_state_json(state_path)
            db2 = TraceDB(tmp_db_path + "_new.db")
            count = db2.import_from_state_json(state_path)
            assert count > 0

            sessions = db2.list_sessions()
            assert len(sessions) >= 1
            qs = db2.list_questions()
            assert len(qs) >= 1
            assert qs[0]["question_text"] == "What DB do we use?"

            db2.close()
        finally:
            for p in [state_path, tmp_db_path + "_new.db"]:
                if os.path.exists(p):
                    os.remove(p)

    def test_import_nonexistent(self, db: TraceDB):
        """Import from a nonexistent path should return 0."""
        count = db.import_from_state_json("/tmp/nonexistent_state.json")
        assert count == 0


# ===================================================================
# Statistics
# ===================================================================


class TestStats:
    def test_stats_empty(self, db: TraceDB):
        s = db.stats()
        assert s["sessions"] == 0
        assert s["phases"] == 0
        assert s["events"] == 0

    def test_stats_populated(self, populated_db: TraceDB):
        s = populated_db.stats()
        assert s["sessions"] >= 1
        assert s["phases"] >= 3
        assert s["events"] >= 1
        assert s["wal_mode"] == "wal"


# ===================================================================
# CLI
# ===================================================================


class TestCLI:
    def test_help(self):
        """CLI --help should work."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "trace_db.py"), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "specback trace DB" in result.stdout

    def test_inspect_empty(self, db: TraceDB):
        """CLI inspect on empty DB should not crash."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "trace_db.py"),
             "--db", db.db_path, "inspect"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_inspect_populated(self, populated_db: TraceDB):
        """CLI inspect on populated DB should show session data."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "trace_db.py"),
             "--db", populated_db.db_path, "inspect"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "test_001" in result.stdout

    def test_stats_cli(self, populated_db: TraceDB):
        """CLI stats should show populated counts."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "trace_db.py"),
             "--db", populated_db.db_path, "stats"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "sessions" in result.stdout

    def test_export_cli(self, populated_db: TraceDB, tmp_db_path: str):
        """CLI export should create a state.json file."""
        import subprocess
        state_path = tmp_db_path + "_cli_state.json"
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "trace_db.py"),
                 "--db", populated_db.db_path,
                 "export", "--state-json", state_path],
                capture_output=True, text=True,
            )
            assert result.returncode == 0
            assert os.path.exists(state_path)
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)

    def test_questions_cli(self, populated_db: TraceDB):
        """CLI questions should list question bank entries."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "trace_db.py"),
             "--db", populated_db.db_path, "questions"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Q-001" in result.stdout


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_spec_unit_invalid_kind_rejected(self, db: TraceDB):
        """Invalid spec unit kind should be rejected by CHECK constraint."""
        with pytest.raises(Exception):
            db.conn.execute(
                "INSERT INTO spec_units (id, kind) VALUES ('bad', 'invalid_kind')"
            )

    def test_question_invalid_severity_rejected(self, db: TraceDB):
        """Invalid severity should be rejected by CHECK constraint."""
        with pytest.raises(Exception):
            db.conn.execute(
                "INSERT INTO questions_bank (id, phase_id, question_text, severity, status) "
                "VALUES ('Q-999', 'ph_01', 'test', 'invalid', 'pending')"
            )

    def test_close_then_operations(self, db: TraceDB):
        """Closing the DB should not crash, and reusing it should work."""
        path = db.db_path
        db.close()
        db2 = TraceDB(path)
        db2.session_start("s_reopen")
        assert db2.get_session("s_reopen") is not None
        db2.close()

    def test_context_manager(self, tmp_db_path: str):
        """TraceDB should work as a context manager."""
        with TraceDB(tmp_db_path) as db:
            db.session_start("s_ctx")
            assert db.get_session("s_ctx") is not None
        # Connection should be closed after context exit
        import sqlite3
        with pytest.raises(sqlite3.ProgrammingError):
            db.conn.execute("SELECT 1")

    def test_session_finish_nonexistent(self, db: TraceDB):
        """session_finish on a nonexistent session should not crash."""
        db.session_finish("nonexistent", ok=True)  # This updates 0 rows, should be fine

    def test_import_empty_json_object(self, tmp_db_path: str):
        """Import from an empty JSON object should return 0."""
        import json, os, tempfile
        fd, state_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(state_path, "w") as f:
            json.dump({}, f)
        try:
            db = TraceDB(tmp_db_path)
            count = db.import_from_state_json(state_path)
            assert count == 0
            # Should NOT have created a session
            assert len(db.list_sessions()) == 0
            db.close()
        finally:
            os.remove(state_path)

    def test_export_import_roundtrip(self, tmp_db_path: str):
        """Export→Import round-trip should preserve all question data."""
        import json, os, tempfile
        # Create source DB with a resolved question
        db1 = TraceDB(tmp_db_path + "_src.db")
        db1.session_start("rt_001")
        db1.phase_start("rt_001", "ph_01", seq=1, name="test", kind="code")
        db1.question_save("Q-RT1", "ph_01", "Round trip test?",
                           severity="critical", status="resolved",
                           resolution="It works!", category="architecture_decision")
        db1.session_finish("rt_001", ok=True)

        # Export
        state_path = tmp_db_path + "_rt.json"
        db1.export_to_state_json(state_path)
        db1.close()

        # Import into a new DB
        db2 = TraceDB(tmp_db_path + "_dst.db")
        count = db2.import_from_state_json(state_path)
        assert count > 0

        # Verify question survived the round trip
        qs = db2.list_questions()
        assert len(qs) == 1
        assert qs[0]["id"] == "Q-RT1"
        assert qs[0]["status"] == "resolved", f"Expected resolved, got {qs[0]['status']}"
        assert qs[0]["resolution"] == "It works!"
        assert qs[0]["question_text"] == "Round trip test?"
        assert qs[0]["severity"] == "critical"

        db2.close()
        for p in [state_path, tmp_db_path + "_src.db", tmp_db_path + "_dst.db"]:
            if os.path.exists(p):
                os.remove(p)

    def test_invalid_phase_kind(self, db: TraceDB):
        """Any phase kind string is accepted (no CHECK constraint)."""
        db.session_start("s_kind")
        # Should not raise — no constraint on kind
        db.phase_start("s_kind", "ph_01", seq=1, name="test",
                        kind="custom_kind", owner="test")

    def test_long_payload_json(self, db_with_session_and_phase: TraceDB):
        """Events with large payloads should not crash."""
        db = db_with_session_and_phase
        large = {"data": "x" * 10000}
        db.event("test_001", "ph_01_recon", type="log", name="large",
                  payload=large)
        evts = db.list_events(adw_id="test_001", limit=20)
        assert len(evts) >= 1


# ===================================================================
# Agent sessions & Processes (SSSF tables)
# ===================================================================


class TestSSSFTables:
    """Tests for agent_sessions and processes tables (SSSF core tables)."""

    def test_agent_session_insert(self, db: TraceDB):
        """agent_sessions should accept a valid row."""
        db.conn.execute(
            """INSERT INTO agent_sessions (adw_id, agent, coding_agent, model,
               session_id, context_tokens, context_window, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test_001", "scout", "pi", "ds-v4-flash",
             "sess_abc", 5000, 128000, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        row = db.conn.execute(
            "SELECT adw_id, agent, model FROM agent_sessions WHERE adw_id=? AND agent=?",
            ("test_001", "scout"),
        ).fetchone()
        assert row is not None
        assert row[0] == "test_001"
        assert row[1] == "scout"

    def test_agent_session_upsert(self, db: TraceDB):
        """agent_sessions uses PRIMARY KEY (adw_id, agent)."""
        import time
        ts = "2026-01-01T00:00:00Z"
        db.conn.execute(
            "INSERT OR REPLACE INTO agent_sessions (adw_id, agent, coding_agent, model, session_id, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s1", "scout", "pi", "v1", "sess_1", ts, ts),
        )
        db.conn.execute(
            "INSERT OR REPLACE INTO agent_sessions (adw_id, agent, coding_agent, model, session_id, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s1", "scout", "pi", "v2", "sess_2", ts, ts),
        )
        row = db.conn.execute(
            "SELECT model, session_id FROM agent_sessions WHERE adw_id='s1' AND agent='scout'"
        ).fetchone()
        assert row[0] == "v2"
        assert row[1] == "sess_2"

    def test_process_insert(self, db: TraceDB):
        """processes should accept a valid row."""
        db.session_start("test_pid")
        db.conn.execute(
            """INSERT INTO processes (adw_id, kind, name, pid, command, started_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test_pid", "adw", "specback_recon", 12345,
             "python adw_specback_recon.py --target .", "2026-01-01T00:00:00Z"),
        )
        row = db.conn.execute(
            "SELECT adw_id, kind, pid FROM processes WHERE adw_id=?", ("test_pid",)
        ).fetchone()
        assert row is not None
        assert row[0] == "test_pid"
        assert row[2] == 12345

    def test_process_lifecycle(self, db: TraceDB):
        """processes should track start and end."""
        db.session_start("test_life")
        db.conn.execute(
            "INSERT INTO processes (adw_id, kind, name, pid, command, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("test_life", "agent", "investigator", 99999, "agent run", "2026-01-01T00:00:00Z"),
        )
        db.conn.execute(
            "UPDATE processes SET ended_at=? WHERE adw_id=? AND kind=? AND name=? AND pid=?",
            ("2026-01-01T01:00:00Z", "test_life", "agent", "investigator", 99999),
        )
        row = db.conn.execute(
            "SELECT pid, ended_at FROM processes WHERE adw_id='test_life' AND kind='agent'"
        ).fetchone()
        assert row[0] == 99999
        assert row[1] is not None


def test_main_accepts_argv() -> None:
    """main(argv) accepts an argv list and exits via argparse (Issue #286)."""
    with pytest.raises(SystemExit) as excinfo:
        trace_db.main([])  # a subcommand is required
    assert excinfo.value.code == 2
