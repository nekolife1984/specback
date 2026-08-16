"""Tests for build-traceability.py --output-dir and --stage arguments."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "build-traceability.py"


def test_help_includes_output_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--output-dir" in result.stdout


def test_help_includes_stage():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--stage" in result.stdout


def test_output_dir_and_stage_with_help_allowed():
    """--output-dir and --stage combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--output-dir", "/tmp/x", "--stage", "drafts", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def _write_minimal_trace(specback_dir: Path) -> None:
    """Write a minimal valid trace.json fixture."""
    trace = {
        "schema_version": "0.2.0",
        "generated_at": "2026-08-16T00:00:00Z",
        "source_units_total": 3,
        "source_units_covered": 1,
        "source_units_excluded": 1,
        "source_units_uncovered": 1,
        "mece_passed": False,
        "by_source": {
            "SRC-0001": {
                "path": "src/app.py",
                "line_range": [1, 10],
                "kind": "function",
                "name": "app_run",
                "covered_by_sections": [{"file": "01-overview.md", "section": "1.1 Intro"}],
                "excluded": False,
                "excluded_reason": None,
            },
            "SRC-0002": {
                "path": "src/util.py",
                "line_range": [5, 8],
                "kind": "function",
                "name": "helper",
                "covered_by_sections": [],
                "excluded": True,
                "excluded_reason": "generated glue",
            },
            "SRC-0003": {
                "path": "src/legacy.py",
                "line_range": [1, 4],
                "kind": "function",
                "name": "old_api",
                "covered_by_sections": [],
                "excluded": False,
                "excluded_reason": None,
            },
        },
        "by_section": {
            "01-overview.md::1.1 Intro": ["SRC-0001"],
        },
        "uncovered_units": ["SRC-0003"],
    }
    specback_dir.mkdir(parents=True, exist_ok=True)
    (specback_dir / "trace.json").write_text(
        json.dumps(trace), encoding="utf-8"
    )


def test_e2e_generates_traceability_md(tmp_path):
    """Full E2E: trace.json in -> traceability.md out with expected content."""
    sb = tmp_path / ".specback"
    out = tmp_path / "out"
    _write_minimal_trace(sb)

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(sb),
         "--output-dir", str(out),
         "--stage", "final"],
        capture_output=True, text=True,
    )
    # mece_passed=False -> exit 1 (expected; file still written)
    assert result.returncode == 1

    report = out / "final" / "traceability.md"
    assert report.exists(), result.stderr
    text = report.read_text(encoding="utf-8")

    # MECE check result rows
    assert "Total extracted source units: **3**" in text
    assert "Covered by the spec: **1 (33.3%)**" in text
    assert "Explicitly excluded: **1**" in text
    assert "❌ FAILED" in text  # mece_passed=False

    # Uncovered list
    assert "### Uncovered list (action required)" in text
    assert "SRC-0003" in text

    # Explicit-exclusion breakdown
    assert "### Explicit-exclusion breakdown" in text
    assert "generated glue" in text

    # Chapter -> Source mapping
    assert "## Chapter → Source mapping" in text
    assert "1.1 Intro" in text
    assert "SRC-0001" in text

    # Source -> Chapter mapping
    assert "## Source → Chapter mapping (by file)" in text
    assert "src/app.py" in text
    assert "src/util.py" in text
    # SRC-0003 (uncovered, not excluded) is intentionally skipped in this
    # mapping (shown in the "Uncovered list" section above instead), so its
    # file appears only as a table cell, not as a section heading.
    assert "### `src/legacy.py`" not in text


def test_e2e_stage_drafts_writes_under_drafts(tmp_path):
    sb = tmp_path / ".specback"
    out = tmp_path / "out"
    _write_minimal_trace(sb)

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(sb),
         "--output-dir", str(out),
         "--stage", "drafts"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert (out / "drafts" / "traceability.md").exists()
    assert not (out / "final" / "traceability.md").exists()


def test_e2e_missing_trace_returns_2(tmp_path):
    sb = tmp_path / ".specback"
    sb.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "not found" in result.stderr


def test_e2e_malformed_trace_clean_error(tmp_path):
    """Missing keys must produce a clean error, not a raw KeyError traceback."""
    sb = tmp_path / ".specback"
    sb.mkdir(parents=True, exist_ok=True)
    (sb / "trace.json").write_text(
        json.dumps({"source_units_total": 1}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "KeyError" not in result.stderr
    assert "Traceback" not in result.stderr
