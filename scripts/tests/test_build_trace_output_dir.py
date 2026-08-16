"""Tests for build-trace.py --output-dir argument."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "build-trace.py"


def test_help_includes_output_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--output-dir" in result.stdout


def test_help_includes_specback_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--specback-dir" in result.stdout


def test_output_dir_with_help_allowed():
    """--output-dir combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", "/tmp/x", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def _write_minimal_source_map(specback_dir: Path) -> None:
    """Write a minimal source-map.json fixture with one Python unit."""
    sm = {
        "schema_version": "0.1.0",
        "target_root": ".",
        "generated_at": "2026-08-16T00:00:00Z",
        "stats": {"files_scanned": 1, "files_excluded": 0,
                  "units_total": 1, "by_kind": {"python_def": 1}},
        "units": [
            {
                "id": "SRC-0001",
                "path": "src/app.py",
                "line_range": [1, 10],
                "kind": "python_def",
                "name": "run",
                "signature": "def run():",
                "fingerprint": "sha1:fake",
            },
        ],
    }
    specback_dir.mkdir(parents=True, exist_ok=True)
    (specback_dir / "source-map.json").write_text(
        json.dumps(sm), encoding="utf-8"
    )


def _write_draft_with_ref(specback_dir: Path) -> None:
    """Write a chapter draft that REFs the fixture unit."""
    drafts = specback_dir / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "01-overview.md").write_text(
        "# Overview\n\n"
        "The entry point is `run`.\n\n"
        "<!-- REF: SRC-0001 -->\n",
        encoding="utf-8",
    )


def test_e2e_generates_trace_json(tmp_path):
    """Full E2E: source-map.json + draft REFs -> trace.json with expected keys."""
    sb = tmp_path / ".specback"
    out = tmp_path / "out"
    _write_minimal_source_map(sb)
    _write_draft_with_ref(sb)

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(sb),
         "--target-dir-for-required", "drafts",
         "--output-dir", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    trace_path = out / "trace.json"
    assert trace_path.exists(), result.stderr
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["source_units_total"] == 1
    assert trace["source_units_covered"] == 1
    assert trace["source_units_uncovered"] == 0
    assert trace["mece_passed"] is True
    assert "by_source" in trace
    assert "by_section" in trace
    assert trace["by_source"]["SRC-0001"]["covered_by_sections"]


def test_e2e_output_dir_writes_trace_json(tmp_path):
    """--output-dir writes trace.json under the given dir."""
    sb = tmp_path / ".specback"
    out = tmp_path / "out"
    _write_minimal_source_map(sb)
    _write_draft_with_ref(sb)

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(sb),
         "--target-dir-for-required", "drafts",
         "--output-dir", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "trace.json").exists(), result.stderr


def test_e2e_missing_source_map_returns_2(tmp_path):
    sb = tmp_path / ".specback"
    sb.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "source-map.json not found" in result.stderr
