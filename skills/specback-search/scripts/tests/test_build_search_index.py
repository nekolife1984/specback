"""Tests for build-search-index.py.

Tests the CLI interface using temp directories with fixture JSON data.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "build-search-index.py"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Help tests
# ---------------------------------------------------------------------------

def test_help():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "specback-dir" in result.stdout
    assert "uncovered" in result.stdout
    assert "confidence" in result.stdout
    assert "questions" in result.stdout
    assert "chapter" in result.stdout
    assert "role" in result.stdout
    assert "drift" in result.stdout
    assert "format" in result.stdout


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_specback_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", "/nonexistent"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "not a directory" in result.stderr


# ---------------------------------------------------------------------------
# Name/path search
# ---------------------------------------------------------------------------

def test_search_by_name(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "User", "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0002" in result.stdout  # User model — exact name match
    assert "app/models/user.py" in result.stdout


def test_search_by_path(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "auth", "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0004" in result.stdout
    assert "app/routes/auth.py" in result.stdout


def test_search_no_match(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ZZZZNOTFOUND", "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no results found" in result.stdout


# ---------------------------------------------------------------------------
# Uncovered filter
# ---------------------------------------------------------------------------

def test_uncovered(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--uncovered", "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0004" in result.stdout
    assert "app/routes/auth.py" in result.stdout
    assert "SRC-0001" not in result.stdout  # covered
    assert "SRC-0002" not in result.stdout  # covered


# ---------------------------------------------------------------------------
# Chapter filter
# ---------------------------------------------------------------------------

def test_filter_by_chapter(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--chapter", "03-data-model",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0002" in result.stdout  # covered by 03-data-model
    assert "SRC-0003" in result.stdout  # covered by 03-data-model
    assert "SRC-0001" not in result.stdout  # covered by 02-feature-specs


# ---------------------------------------------------------------------------
# Role filter
# ---------------------------------------------------------------------------

def test_filter_by_role(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--role", "model",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0002" in result.stdout  # model
    assert "SRC-0003" in result.stdout  # model
    assert "SRC-0001" not in result.stdout  # endpoint


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def test_questions_open(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--questions", "open",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Q-001" in result.stdout
    assert "注文のキャンセル期限" in result.stdout
    assert "Q-002" not in result.stdout  # answered


def test_questions_all(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--questions", "all",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Q-001" in result.stdout
    assert "Q-002" in result.stdout


# ---------------------------------------------------------------------------
# Drift report
# ---------------------------------------------------------------------------

def test_drift(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--drift",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Changed files" in result.stdout
    assert "3" in result.stdout


def test_drift_missing(tmp_path: Path):
    """No drift-report.json -> no drift output."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    (sb / "inventory.json").write_text(json.dumps(INVENTORY), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--drift",
         "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "run detect-drift.py first" in result.stdout


# ---------------------------------------------------------------------------
# JSON output format
# ---------------------------------------------------------------------------

def test_json_output(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "User", "--format", "json",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "results" in data
    assert len(data["results"]) >= 1
    first = data["results"][0]
    assert "src_id" in first
    assert "name" in first
    assert "path" in first
    assert "line_range" in first
    assert "role" in first


# ---------------------------------------------------------------------------
# Compound filters
# ---------------------------------------------------------------------------

def test_uncovered_with_role(specback_dir: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--uncovered", "--role", "endpoint",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0004" in result.stdout  # uncovered + endpoint


def test_query_with_uncovered(specback_dir: Path):
    """Query + uncovered: intersection of both filters."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "auth", "--uncovered",
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0004" in result.stdout  # matches "auth" AND uncovered


# ---------------------------------------------------------------------------
# Missing optional files
# ---------------------------------------------------------------------------

def test_missing_inventory(tmp_path: Path):
    """Should still work without inventory.json."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "User",
         "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "SRC-0002" in result.stdout


def test_missing_questions(tmp_path: Path):
    """Should still work without questions.json."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    (sb / "source-map.json").write_text(json.dumps(SOURCE_MAP), encoding="utf-8")
    (sb / "trace.json").write_text(json.dumps(TRACE), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--questions", "open",
         "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "none" in result.stdout


# ---------------------------------------------------------------------------
# No filters = help message
# ---------------------------------------------------------------------------

def test_no_filters(specback_dir: Path):
    """Running with no query and no flags should show a hint."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(specback_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Hint" in result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# CONFIDENCE_RE: SRC-ID support (Issue #224 follow-up)
# ═══════════════════════════════════════════════════════════════════════════


class TestConfidenceRe:
    """Verify CONFIDENCE_RE matches both path:line and SRC-ID REF formats."""

    def setup_method(self):
        # Replicate the exact regex from build-search-index.py
        import re
        self._re = re.compile(
            r'<!-- REF:\s*(?:\S+:\d+(?:-\d+)?|SRC-\d+)\s*-->\s*([🟢🟡🔴])'
        )

    def test_path_line_range(self):
        m = self._re.search('<!-- REF: src/errors.py:1-50 --> 🟢')
        assert m is not None
        assert m.group(1) == "🟢"

    def test_path_line_single(self):
        m = self._re.search('<!-- REF: app/users.py:42 --> 🟡')
        assert m is not None
        assert m.group(1) == "🟡"

    def test_src_id(self):
        m = self._re.search('<!-- REF: SRC-0001 --> 🟢')
        assert m is not None
        assert m.group(1) == "🟢"

    def test_src_id_red(self):
        m = self._re.search('<!-- REF: SRC-0142 --> 🔴')
        assert m is not None
        assert m.group(1) == "🔴"

    def test_no_emoji_no_match(self):
        m = self._re.search('<!-- REF: SRC-0001 -->')
        assert m is None

    def test_invalid_src_id_no_match(self):
        m = self._re.search('<!-- REF: SRC-XXX --> 🟢')
        assert m is None


class TestConfCommentRe:
    """Verify CONF_COMMENT_RE maps the phase-doc comment form into a confidence.

    #360: chapters written with ``<!-- CONFIDENCE: HIGH | MED | LOW -->`` must
    still surface a confidence in search even when no emoji sits right after the
    REF. We import the real module (stdlib-only deps) and exercise its regex and
    emoji mapping directly.
    """

    @staticmethod
    def _load_bsi():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bsi", str(SCRIPT)
        )
        sys.modules["bsi"] = spec.loader.create_module(spec) if hasattr(spec.loader, "create_module") else None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bsi"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_comment_form_higher_map(self):
        bsi = self._load_bsi()
        m = bsi.CONF_COMMENT_RE.search("<!-- CONFIDENCE: HIGH -->")
        assert m is not None
        assert bsi.CONF_COMMENT_TO_EMOJI[m.group(1).upper()] == "🟢"

    def test_comment_form_mapping_table(self):
        bsi = self._load_bsi()
        assert bsi.CONF_COMMENT_TO_EMOJI["HIGH"] == "🟢"
        assert bsi.CONF_COMMENT_TO_EMOJI["MED"] == "🟡"
        assert bsi.CONF_COMMENT_TO_EMOJI["LOW"] == "🔴"
        assert bsi.CONF_COMMENT_TO_EMOJI["INFERRED"] == "🟡"

    def test_comment_form_lowercase_legacy(self):
        bsi = self._load_bsi()
        m = bsi.CONF_COMMENT_RE.search("<!-- CONFIDENCE: assumed -->")
        assert m is not None
        assert bsi.CONF_COMMENT_TO_EMOJI[m.group(1).upper()] == "🔴"
