#!/usr/bin/env python3
"""
trace_db.py — SQLite trace DB for specback session state.

Replaces the monolithic ``state.json`` with a structured SQLite database
that records every session, phase, event, envelope, gate result, and
specback-specific artifact (spec units, chapter drafts, question bank).

This is a **Phase 1 + Phase 2** implementation of the SSSF-inspired
trace DB pattern:

  **Phase 1** — SQLite schema + CRUD layer (this file)
  **Phase 2** — Bidirectional sync with legacy ``state.json``

Design principles
-----------------
1. **stdlib only** — sqlite3 is built into Python; no PyPI dependencies.
2. **WAL mode** — the UI can read while the agent writes, no locks.
3. **Append-only events** — events are never mutated; new rows only.
4. **Specback-specific tables** extend the standard SSSF schema with
   ``spec_units``, ``chapter_drafts``, and ``questions_bank``.
5. **Backward compatible** — a sync layer reads/writes ``state.json`` so
   existing scripts continue working during migration.

Usage
-----
    # Create / open a trace DB
    db = TraceDB(".specback/trace.db")

    # Record a session
    db.session_start("adw_001", engineer="hermes")

    # Record a phase
    db.phase_start("adw_001", "phase_01_recon", seq=1, name="recon",
                   kind="agent", owner="scout")

    # Record an event
    db.event("adw_001", "phase_01_recon", type="log", name="scout",
             payload={"files_found": 42})

    # Bidirectional sync
    db.export_to_state_json(".specback/state.json")
    db.import_from_state_json(".specback/state.json")

CLI
---
    # Inspect the trace DB
    python3 trace_db.py --db .specback/trace.db inspect

    # Export to state.json
    python3 trace_db.py --db .specback/trace.db export --state-json .specback/state.json

    # Import from state.json
    python3 trace_db.py --db .specback/trace.db import --state-json .specback/state.json

    # Show summary stats
    python3 trace_db.py --db .specback/trace.db stats
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


# ===================================================================
# SQL Schema
# ===================================================================

CORE_SCHEMA = """
-- Sessions: one row per specback run
CREATE TABLE IF NOT EXISTS sessions (
    adw_id        TEXT PRIMARY KEY,
    adw_name      TEXT,
    request       TEXT,
    status        TEXT DEFAULT 'running',
    engineer      TEXT DEFAULT 'hermes',
    started_at    TEXT,
    ended_at      TEXT,
    total_tokens  INTEGER DEFAULT 0,
    total_cost    REAL DEFAULT 0.0,
    archived      INTEGER DEFAULT 0
);

-- Phases: one row per phase execution
CREATE TABLE IF NOT EXISTS phases (
    phase_id      TEXT PRIMARY KEY,
    adw_id        TEXT REFERENCES sessions(adw_id),
    seq           INTEGER,
    name          TEXT,
    kind          TEXT,
    owner         TEXT,
    description   TEXT,
    status        TEXT DEFAULT 'running',
    attempt       INTEGER DEFAULT 0,
    retries       INTEGER DEFAULT 0,
    error         TEXT,
    started_at    TEXT,
    ended_at      TEXT
);

-- Events: append-only event log
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    adw_id        TEXT REFERENCES sessions(adw_id),
    phase_id      TEXT REFERENCES phases(phase_id),
    parent_id     TEXT,
    type          TEXT,
    name          TEXT,
    payload_json  TEXT,
    tokens        INTEGER,
    started_at    TEXT,
    ended_at      TEXT
);

-- Envelopes: typed data passed between phases
CREATE TABLE IF NOT EXISTS envelopes (
    envelope_id   TEXT PRIMARY KEY,
    adw_id        TEXT REFERENCES sessions(adw_id),
    phase_id      TEXT REFERENCES phases(phase_id),
    agent         TEXT,
    output_type   TEXT,
    payload_json  TEXT,
    valid         INTEGER,
    attempt       INTEGER,
    created_at    TEXT
);

-- Gate results: phase-quality check outcomes
CREATE TABLE IF NOT EXISTS gate_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    adw_id        TEXT REFERENCES sessions(adw_id),
    phase_id      TEXT REFERENCES phases(phase_id),
    attempt       INTEGER,
    gate          TEXT,
    passed        INTEGER,
    violations_json TEXT,
    checks_json   TEXT,
    created_at    TEXT
);

-- Agent sessions: per-coding-agent metadata
CREATE TABLE IF NOT EXISTS agent_sessions (
    adw_id        TEXT,
    agent         TEXT,
    coding_agent  TEXT,
    model         TEXT,
    color         TEXT,
    session_id    TEXT,
    context_tokens INTEGER,
    context_window INTEGER,
    created_at    TEXT,
    last_used_at  TEXT,
    PRIMARY KEY (adw_id, agent)
);

-- Processes: tracked subprocesses
CREATE TABLE IF NOT EXISTS processes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    adw_id        TEXT REFERENCES sessions(adw_id),
    kind          TEXT,
    name          TEXT,
    pid           INTEGER,
    command       TEXT,
    started_at    TEXT,
    ended_at      TEXT
);
"""

SPECBACK_EXTENSION_SCHEMA = """
-- Spec units: chapters, source units, and questions tracked by specback
CREATE TABLE IF NOT EXISTS spec_units (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('chapter', 'source_unit', 'question')),
    status        TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'verified', 'failed', 'abandoned')),
    confidence    TEXT DEFAULT 'ASSUMED'
                    CHECK (confidence IN ('VERIFIED', 'INFERRED', 'ASSUMED')),
    envelope_id   TEXT REFERENCES envelopes(envelope_id),
    label         TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

-- Chapter drafts: per-chapter quality metrics
CREATE TABLE IF NOT EXISTS chapter_drafts (
    id            TEXT PRIMARY KEY,
    phase_id      TEXT REFERENCES phases(phase_id),
    content_hash  TEXT,
    ref_count     INTEGER DEFAULT 0,
    code_block_lines INTEGER DEFAULT 0,
    total_lines   INTEGER DEFAULT 0,
    confidence_ratio REAL DEFAULT 0.0,
    mermaid_count INTEGER DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT
);

-- Question bank: specback questions and resolutions
CREATE TABLE IF NOT EXISTS questions_bank (
    id            TEXT PRIMARY KEY,
    phase_id      TEXT REFERENCES phases(phase_id),
    question_text TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('critical', 'important', 'nice_to_have')),
    status        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'abandoned')),
    resolution    TEXT,
    category      TEXT,
    created_at    TEXT,
    resolved_at   TEXT
);
"""

# Additive migrations for schema evolution
MIGRATIONS: list[tuple[str, str, str]] = [
    # (table_name, column_name, column_declaration)
    ("sessions", "archived", "INTEGER DEFAULT 0"),
    ("spec_units", "label", "TEXT"),
    ("chapter_drafts", "mermaid_count", "INTEGER DEFAULT 0"),
]


# ===================================================================
# Helpers
# ===================================================================

def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(length: int = 8) -> str:
    """Return a hex random id of given length."""
    import secrets
    return secrets.token_hex(length // 2)


def _ensure_dir(path: Path) -> None:
    """Create parent directories if they don't exist."""
    path.parent.mkdir(parents=True, exist_ok=True)


# ===================================================================
# TraceDB
# ===================================================================

class TraceDB:
    """SQLite trace database for specback session state.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file. Created if it doesn't exist.
        Parent directories are created automatically.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        _ensure_dir(Path(self.db_path))

        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute("PRAGMA foreign_keys=ON;")

        self.conn.executescript(CORE_SCHEMA)
        self.conn.executescript(SPECBACK_EXTENSION_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Apply additive column migrations for schema evolution."""
        for table, column, decl in MIGRATIONS:
            cursor = self.conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in cursor.fetchall()}
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def __enter__(self) -> TraceDB:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit — close the connection."""
        self.close()

    # ── Sessions ──────────────────────────────────────────────────────────

    def session_start(
        self,
        adw_id: str,
        engineer: str = "hermes",
        adw_name: str | None = None,
        request: str | None = None,
    ) -> None:
        """Record the start of a session."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO sessions (adw_id, status, engineer, started_at, adw_name, request)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(adw_id) DO UPDATE SET status='running'""",
            (adw_id, "running", engineer, now, adw_name, request),
        )

    def session_finish(self, adw_id: str, ok: bool = True) -> None:
        """Mark a session as finished."""
        now = _now_iso()
        self.conn.execute(
            "UPDATE sessions SET status=?, ended_at=? WHERE adw_id=?",
            ("success" if ok else "fail", now, adw_id),
        )

    def session_add_usage(self, adw_id: str, tokens: int, cost: float) -> None:
        """Accumulate token/cost usage for a session."""
        self.conn.execute(
            "UPDATE sessions SET total_tokens = total_tokens + ?, total_cost = total_cost + ? WHERE adw_id=?",
            (tokens, cost, adw_id),
        )

    def session_set_request(self, adw_id: str, request: str) -> None:
        """Set the request/prompt for a session."""
        self.conn.execute(
            "UPDATE sessions SET request=? WHERE adw_id=?",
            (request, adw_id),
        )

    def get_session(self, adw_id: str) -> dict[str, Any] | None:
        """Get a session row as a dict, or None if not found."""
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE adw_id=?", (adw_id,)
        ).fetchone()
        if row is None:
            return None
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(sessions)").fetchall()]
        return dict(zip(cols, row))

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions."""
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(sessions)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Phases ────────────────────────────────────────────────────────────

    def phase_start(
        self,
        adw_id: str,
        phase_id: str,
        seq: int,
        name: str,
        kind: str = "code",
        owner: str | None = None,
        description: str = "",
    ) -> None:
        """Record the start of a phase."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description,
               status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (phase_id, adw_id, seq, name, kind, owner, description, now),
        )
        self.event(
            adw_id=adw_id, phase_id=phase_id,
            type="phase_start", name=name,
            payload={"kind": kind, "owner": owner, "description": description},
        )

    def phase_finish(
        self,
        phase_id: str,
        ok: bool = True,
        error: str | None = None,
        retries: int = 0,
    ) -> None:
        """Mark a phase as finished."""
        now = _now_iso()
        status = "success" if ok else ("error" if error else "fail")
        self.conn.execute(
            "UPDATE phases SET status=?, ended_at=?, error=?, retries=? WHERE phase_id=?",
            (status, now, error, retries, phase_id),
        )
        self.event(
            adw_id=self._adw_for_phase(phase_id),
            phase_id=phase_id,
            type="phase_end", name=status,
            payload={"status": status},
        )

    def get_phase(self, phase_id: str) -> dict[str, Any] | None:
        """Get a phase row as a dict, or None if not found."""
        row = self.conn.execute(
            "SELECT * FROM phases WHERE phase_id=?", (phase_id,)
        ).fetchone()
        if row is None:
            return None
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(phases)").fetchall()]
        return dict(zip(cols, row))

    def list_phases(self, adw_id: str) -> list[dict[str, Any]]:
        """List phases for a session."""
        rows = self.conn.execute(
            "SELECT * FROM phases WHERE adw_id=? ORDER BY seq", (adw_id,)
        ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(phases)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Events ────────────────────────────────────────────────────────────

    def event(
        self,
        adw_id: str,
        phase_id: str | None = None,
        parent_id: str | None = None,
        type: str = "log",
        name: str = "",
        payload: dict[str, Any] | None = None,
        tokens: int | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> str:
        """Record an event. Returns the event_id."""
        event_id = f"evt_{_new_id(12)}"
        now = _now_iso()
        payload_json = json.dumps(payload or {})
        self.conn.execute(
            """INSERT INTO events (event_id, adw_id, phase_id, parent_id, type, name,
               payload_json, tokens, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, adw_id, phase_id, parent_id, type, name,
             payload_json, tokens, started_at or now, ended_at),
        )
        return event_id

    def list_events(
        self,
        adw_id: str | None = None,
        phase_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List events, optionally filtered by session or phase."""
        if phase_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE phase_id=? ORDER BY started_at DESC LIMIT ?",
                (phase_id, limit),
            ).fetchall()
        elif adw_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE adw_id=? ORDER BY started_at DESC LIMIT ?",
                (adw_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(events)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Envelopes ─────────────────────────────────────────────────────────

    def envelope_save(
        self,
        envelope_id: str,
        adw_id: str,
        phase_id: str | None = None,
        agent: str | None = None,
        output_type: str | None = None,
        payload: dict[str, Any] | None = None,
        valid: bool = True,
        attempt: int = 0,
    ) -> None:
        """Save an envelope."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO envelopes (envelope_id, adw_id, phase_id, agent, output_type,
               payload_json, valid, attempt, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (envelope_id, adw_id, phase_id, agent, output_type,
             json.dumps(payload or {}), 1 if valid else 0, attempt, now),
        )

    def list_envelopes(self, adw_id: str) -> list[dict[str, Any]]:
        """List envelopes for a session."""
        rows = self.conn.execute(
            "SELECT * FROM envelopes WHERE adw_id=? ORDER BY created_at", (adw_id,)
        ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(envelopes)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Gate results ──────────────────────────────────────────────────────

    def gate_result(
        self,
        adw_id: str,
        phase_id: str,
        gate: str,
        passed: bool,
        violations: list[str] | None = None,
        checks: list[dict[str, Any]] | None = None,
        attempt: int = 0,
    ) -> None:
        """Record a gate check result."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed,
               violations_json, checks_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (adw_id, phase_id, attempt, gate, 1 if passed else 0,
             json.dumps(violations or []), json.dumps(checks or []), now),
        )

    def list_gate_results(self, adw_id: str) -> list[dict[str, Any]]:
        """List gate results for a session."""
        rows = self.conn.execute(
            "SELECT * FROM gate_results WHERE adw_id=? ORDER BY created_at", (adw_id,)
        ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(gate_results)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Specback extensions ───────────────────────────────────────────────

    def spec_unit_save(
        self,
        unit_id: str,
        kind: Literal["chapter", "source_unit", "question"],
        status: Literal["draft", "verified", "failed", "abandoned"] = "draft",
        confidence: Literal["VERIFIED", "INFERRED", "ASSUMED"] = "ASSUMED",
        envelope_id: str | None = None,
        label: str | None = None,
    ) -> None:
        """Create or update a spec unit (chapter, source_unit, or question)."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO spec_units (id, kind, status, confidence, envelope_id, label, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status=excluded.status,
                   confidence=excluded.confidence,
                   envelope_id=excluded.envelope_id,
                   label=excluded.label,
                   updated_at=excluded.updated_at""",
            (unit_id, kind, status, confidence, envelope_id, label, now, now),
        )

    def chapter_draft_save(
        self,
        chapter_id: str,
        phase_id: str,
        content_hash: str = "",
        ref_count: int = 0,
        code_block_lines: int = 0,
        total_lines: int = 0,
        confidence_ratio: float = 0.0,
        mermaid_count: int = 0,
    ) -> None:
        """Record or update a chapter draft's quality metrics."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO chapter_drafts (id, phase_id, content_hash, ref_count,
               code_block_lines, total_lines, confidence_ratio, mermaid_count,
               created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   content_hash=excluded.content_hash,
                   ref_count=excluded.ref_count,
                   code_block_lines=excluded.code_block_lines,
                   total_lines=excluded.total_lines,
                   confidence_ratio=excluded.confidence_ratio,
                   mermaid_count=excluded.mermaid_count,
                   updated_at=excluded.updated_at""",
            (chapter_id, phase_id, content_hash, ref_count,
             code_block_lines, total_lines, confidence_ratio, mermaid_count,
             now, now),
        )

    def question_save(
        self,
        qid: str,
        phase_id: str,
        question_text: str,
        severity: Literal["critical", "important", "nice_to_have"] = "important",
        status: Literal["pending", "resolved", "abandoned"] = "pending",
        resolution: str | None = None,
        category: str | None = None,
    ) -> None:
        """Create or update a question bank entry."""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO questions_bank (id, phase_id, question_text, severity, status,
               resolution, category, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status=excluded.status,
                   resolution=excluded.resolution,
                   severity=excluded.severity,
                   category=excluded.category,
                   resolved_at=CASE
                       WHEN excluded.status='resolved' AND questions_bank.status!='resolved'
                       THEN excluded.resolved_at
                       ELSE questions_bank.resolved_at
                   END""",
            (qid, phase_id, question_text, severity, status,
             resolution, category, now, now if status == "resolved" else None),
        )

    def list_questions(self, status: str | None = None) -> list[dict[str, Any]]:
        """List questions, optionally filtered by status."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM questions_bank WHERE status=? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM questions_bank ORDER BY id"
            ).fetchall()
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(questions_bank)").fetchall()]
        return [dict(zip(cols, r)) for r in rows]

    # ── Bidirectional sync with state.json ────────────────────────────────

    def export_to_state_json(self, state_json_path: str | Path) -> dict[str, Any]:
        """Export the most recent session to a state.json-compatible dict.

        This is the bridge from SQLite → state.json.
        Returns the exported dict for further processing.
        """
        sessions = self.list_sessions(limit=1)
        if not sessions:
            return {}

        session = sessions[0]
        phases = self.list_phases(session["adw_id"])
        events = self.list_events(adw_id=session["adw_id"], limit=100)
        questions = self.list_questions()

        # Build phase_progress from phases
        phase_progress: dict[str, dict[str, Any]] = {}
        for p in phases:
            key = f"phase_{p['seq']}"
            phase_progress[key] = {
                "total_subtasks": 1,
                "completed_subtasks": 1 if p["status"] == "success" else 0,
                "blocked_subtasks": [],
            }

        # Build session_history from events
        session_history: list[dict[str, Any]] = [
            {
                "timestamp": e["started_at"],
                "phase": e["phase_id"][-2:] if e["phase_id"] else 0,
                "event": f"{e['type']}: {e['name']}" if e['name'] else e['type'],
            }
            for e in events
        ]

        state = {
            "current_phase": max([p["seq"] for p in phases], default=0),
            "phase_progress": phase_progress,
            "all_quality_gates_passed": all(
                p["status"] == "success" for p in phases
            ),
            "started_at": session.get("started_at") or _now_iso(),
            "last_updated": _now_iso(),
            "session_history": session_history,
            "adw_id": session["adw_id"],
            "trace_db_path": self.db_path,
            "trace_db_version": 1,
        }

        # Export questions if present
        if questions:
            state["questions"] = [
                {
                    "id": q["id"],
                    "body": q["question_text"],
                    "severity": q["severity"],
                    "status": "open" if q["status"] == "pending" else q["status"],
                    "resolution": q["resolution"],
                    "category": q["category"],
                }
                for q in questions
            ]

        # Export chapter draft metrics if present
        chapters = self.conn.execute(
            "SELECT * FROM chapter_drafts ORDER BY id"
        ).fetchall()
        if chapters:
            chapter_cols = [d[1] for d in self.conn.execute("PRAGMA table_info(chapter_drafts)").fetchall()]
            state["chapter_drafts"] = {
                c["id"]: {
                    "content_hash": c["content_hash"],
                    "ref_count": c["ref_count"],
                    "total_lines": c["total_lines"],
                    "confidence_ratio": c["confidence_ratio"],
                }
                for c in (dict(zip(chapter_cols, r)) for r in chapters)
            }

        # Write to file if path provided
        fpath = Path(state_json_path)
        _ensure_dir(fpath)
        fpath.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

        return state

    def import_from_state_json(self, state_json_path: str | Path) -> int:
        """Import state from a state.json file into the trace DB.

        Returns the number of records imported (0 if file not found).
        This is the bridge from state.json → SQLite.
        """
        fpath = Path(state_json_path)
        if not fpath.exists():
            return 0

        state = json.loads(fpath.read_text(encoding="utf-8"))
        if not state or not isinstance(state, dict) or "current_phase" not in state:
            return 0

        adw_id = state.get("adw_id", f"imported_{_new_id(8)}")

        # Create/update session
        self.session_start(
            adw_id=adw_id,
            engineer="hermes",
            adw_name="imported",
            request="Imported from state.json",
        )

        # Record phase progress
        current_phase = state.get("current_phase", 0)
        phase_progress = state.get("phase_progress", {})

        # Create a single consolidated phase from the state
        phase_id = f"{adw_id}_00_state_import"
        self.phase_start(
            adw_id=adw_id,
            phase_id=phase_id,
            seq=current_phase,
            name=f"phase_{current_phase}",
            kind="code",
            owner="agent",
            description=f"Imported phase {current_phase} from state.json",
        )

        phases_completed = sum(
            1 for v in phase_progress.values()
            if isinstance(v, dict) and v.get("completed_subtasks", 0) > 0
        )
        self.phase_finish(
            phase_id=phase_id,
            ok=(phases_completed >= current_phase),
        )

        # Record session history as events
        for entry in state.get("session_history", []):
            self.event(
                adw_id=adw_id,
                phase_id=phase_id,
                type="log",
                name=entry.get("event", ""),
                payload={"phase": entry.get("phase")},
                started_at=entry.get("timestamp"),
            )

        # Record spec units if present
        for unit in state.get("spec_units", []):
            self.spec_unit_save(
                unit_id=unit.get("id", f"unit_{_new_id(8)}"),
                kind=unit.get("kind", "chapter"),
                status=unit.get("status", "draft"),
                confidence=unit.get("confidence", "ASSUMED"),
                label=unit.get("label"),
            )

        # Record questions if present
        for q in state.get("questions", []):
            q_status_map: dict[str, Literal["pending", "resolved", "abandoned"]] = {
                "open": "pending", "answered": "resolved",
                "resolved": "resolved",
                "abandoned": "abandoned", "skipped": "abandoned"}
            self.question_save(
                qid=q.get("id", f"Q-{_new_id(3)}"),
                phase_id=phase_id,
                question_text=q.get("body", ""),
                severity=q.get("severity", "important"),
                status=q_status_map.get(q.get("status", "open"), "pending"),
                resolution=q.get("answer", q.get("resolution")),
                category=q.get("category"),
            )

        # Record chapter drafts if present
        for chapter_id, metrics in state.get("chapter_drafts", {}).items():
            self.chapter_draft_save(
                chapter_id=chapter_id,
                phase_id=phase_id,
                content_hash=metrics.get("content_hash", ""),
                ref_count=metrics.get("ref_count", 0),
                total_lines=metrics.get("total_lines", 0),
                confidence_ratio=metrics.get("confidence_ratio", 0.0),
            )

        # Mark session as finished
        self.session_finish(adw_id, ok=True)

        return len(state.get("session_history", [])) + int(state != {})

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return summary statistics about the trace DB."""
        session_count = self.conn.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0]
        phase_count = self.conn.execute(
            "SELECT COUNT(*) FROM phases"
        ).fetchone()[0]
        event_count = self.conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        question_count = self.conn.execute(
            "SELECT COUNT(*) FROM questions_bank"
        ).fetchone()[0]
        chapter_count = self.conn.execute(
            "SELECT COUNT(*) FROM chapter_drafts"
        ).fetchone()[0]
        gate_count = self.conn.execute(
            "SELECT COUNT(*) FROM gate_results"
        ).fetchone()[0]

        return {
            "db_path": self.db_path,
            "sessions": session_count,
            "phases": phase_count,
            "events": event_count,
            "questions": question_count,
            "chapter_drafts": chapter_count,
            "gate_results": gate_count,
            "wal_mode": self.conn.execute("PRAGMA journal_mode").fetchone()[0],
        }

    def _adw_for_phase(self, phase_id: str) -> str:
        """Get the session adw_id for a given phase_id."""
        row = self.conn.execute(
            "SELECT adw_id FROM phases WHERE phase_id=?", (phase_id,)
        ).fetchone()
        return row[0] if row else "unknown"


# ===================================================================
# CLI
# ===================================================================

def _cli_inspect(db: TraceDB, args: argparse.Namespace) -> None:
    """Inspect the trace DB contents."""
    stats = db.stats()
    print(f"Trace DB: {stats['db_path']}")
    print(f"  WAL mode: {stats['wal_mode']}")
    print(f"  Sessions: {stats['sessions']}")
    print(f"  Phases:   {stats['phases']}")
    print(f"  Events:   {stats['events']}")
    print(f"  Gates:    {stats['gate_results']}")
    print(f"  Questions: {stats['questions']}")
    print(f"  Chapters: {stats['chapter_drafts']}")
    print()

    sessions = db.list_sessions(limit=5)
    if sessions:
        print("Recent sessions:")
        for s in sessions:
            print(f"  {s['adw_id']:20s} | {s['status']:10s} | {s.get('started_at', '')}")
    else:
        print("No sessions found.")

    if args.verbose and sessions:
        for s in sessions:
            phases = db.list_phases(s["adw_id"])
            if phases:
                print(f"\n  Phases for {s['adw_id']}:")
                for p in phases:
                    print(f"    {p['phase_id'][-32:]:32s} | {p['status']:10s} | {p['name']:15s} | kind={p['kind']}")


def _cli_export(db: TraceDB, args: argparse.Namespace) -> None:
    """Export trace DB to state.json."""
    state_json = args.state_json
    state = db.export_to_state_json(state_json)
    if state:
        print(f"Exported to {state_json} ({len(state)} fields)")
    else:
        print("No sessions to export.")


def _cli_import(db: TraceDB, args: argparse.Namespace) -> None:
    """Import from state.json into trace DB."""
    count = db.import_from_state_json(args.state_json)
    print(f"Imported {count} records from {args.state_json}")


def _cli_stats(db: TraceDB, args: argparse.Namespace) -> None:
    """Show trace DB statistics."""
    s = db.stats()
    for k, v in s.items():
        print(f"{k:20s} = {v}")


def _cli_questions(db: TraceDB, args: argparse.Namespace) -> None:
    """List questions from the trace DB."""
    questions = db.list_questions(status=args.status)
    if not questions:
        print("No questions found.")
        return
    for q in questions:
        print(f"  {q['id']:8s} | {q['status']:10s} | {q['severity']:15s} | {q['question_text'][:60]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="specback trace DB — SQLite-backed session state",
    )
    parser.add_argument(
        "--db", default=".specback/trace.db",
        help="Path to the SQLite trace database (default: .specback/trace.db)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect the trace DB contents")
    p_inspect.add_argument("--verbose", "-v", action="store_true", help="Show detailed phase info")
    p_inspect.set_defaults(func=_cli_inspect)

    # export
    p_export = sub.add_parser("export", help="Export to state.json")
    p_export.add_argument("--state-json", default=".specback/state.json",
                          help="Path to state.json (default: .specback/state.json)")
    p_export.set_defaults(func=_cli_export)

    # import
    p_import = sub.add_parser("import", help="Import from state.json")
    p_import.add_argument("--state-json", default=".specback/state.json",
                          help="Path to state.json (default: .specback/state.json)")
    p_import.set_defaults(func=_cli_import)

    # stats
    p_stats = sub.add_parser("stats", help="Show trace DB statistics")
    p_stats.set_defaults(func=_cli_stats)

    # questions
    p_q = sub.add_parser("questions", help="List questions in the trace DB")
    p_q.add_argument("--status", default=None, choices=["pending", "resolved", "abandoned"],
                     help="Filter by status")
    p_q.set_defaults(func=_cli_questions)

    args = parser.parse_args(argv)
    db = TraceDB(args.db)

    try:
        args.func(db, args)
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
