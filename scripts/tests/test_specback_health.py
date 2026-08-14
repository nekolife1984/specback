#!/usr/bin/env python3
"""Tests for specback-health.py — spec health report (Issue #268).

Follows the importlib pattern of test_specback_estimate.py: the script is
loaded as a module so its functions (``scan_chapter``, ``overall_score``,
``rating_for``, ...) can be unit-tested directly, while CLI-level behaviour
(exit codes, --json, --min-health-score) is exercised via subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(scripts_dir))

_spec = importlib.util.spec_from_file_location(
    "specback_health", scripts_dir / "specback-health.py"
)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["specback_health"] = mod  # register before exec_module
_spec.loader.exec_module(mod)

SCRIPT = scripts_dir / "specback-health.py"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


GOOD_CHAPTER = """# Chapter 1: Overview

This system handles user authentication.

🟢 VERIFIED: the login endpoint exists in src/auth.py.
🟢 VERIFIED: the session store uses Redis.
🟡 INFERRED: session expiry is likely 30 minutes.
🔴 ASSUMED: rate limiting is applied per IP.

<!-- REF: SRC-0001 -->
<!-- REF: src/auth.py:10-25 -->

```python
# This code fence must NOT be counted:
🟢 VERIFIED inside fence
🔴 ASSUMED inside fence
<!-- REF: fake -->
```
"""

BAD_CHAPTER = """# Chapter 2: Data Model

<!-- CONFIDENCE: verified -->
The User table is defined in src/models.py.
<!-- REF: SRC-0005 -->
<!-- REF: SRC-0006 -->
<!-- REF: SRC-0007 -->

🔴 ASSUMED: the audit log is written synchronously.
🔴 ASSUMED: soft delete is used for all tables.
<!-- BLOCKED: see Q-0012 -->
<!-- ASK SME: multi-tenant isolation -->
"""

# Real producer shape: build-trace.py writes source_units_* keys, NOT
# mece_coverage_rate (that key only exists in coverage-check's JSON output).
REAL_TRACE = {
    "schema_version": "0.2.0",
    "generated_at": "2026-08-11T00:00:00+00:00",
    "source_units_total": 10,
    "source_units_covered": 8,
    "source_units_excluded": 1,
    "source_units_uncovered": 1,
    "mece_passed": True,
    "by_section": {"01-overview.md::Overview": ["SRC-0001"]},
    "by_source": {},
    "uncovered_units": [],
}

REAL_INVENTORY = {
    "units": [
        {
            "id": "INV-0001",
            "type": "action",
            "name": "login",
            "file": "src/auth.py",
            "line": 10,
            "related_source_ids": ["SRC-0001"],
        },
        {
            "id": "INV-0002",
            "type": "entity",
            "name": "User",
            "file": "src/models.py",
            "line": 20,
            "related_source_ids": ["SRC-0005"],
        },
    ]
}

REAL_DRIFT = {
    "schema_version": "1.0.0",
    "generated_at": "2026-08-11T00:00:00+00:00",
    "base": "abc123",
    "summary": {
        "changed_files": 2,
        "affected_spec_sections": 1,
        "new_uncovered_sources": 0,
        "deleted_sources_with_refs": 0,
        "no_impact_changes": 1,
    },
}


def make_specback(tmp_path: Path, with_inventory: bool = True) -> Path:
    """Create a ``.specback/`` fixture dir with two chapters + aux JSON."""
    d = tmp_path / ".specback"
    (d / "drafts").mkdir(parents=True, exist_ok=True)
    (d / "drafts" / "01-overview.md").write_text(GOOD_CHAPTER, encoding="utf-8")
    (d / "drafts" / "02-data-model.md").write_text(BAD_CHAPTER, encoding="utf-8")
    _write_json(
        d / "questions.json",
        [
            {"id": "Q-0001", "question": "Session expiry?", "status": "open"},
            {"id": "Q-0002", "question": "Rate limit value?", "status": "resolved"},
            {"id": "Q-0003", "question": "Audit retention?", "status": "open"},
        ],
    )
    _write_json(d / "trace.json", REAL_TRACE)
    _write_json(d / "drift-report.json", REAL_DRIFT)
    _write_json(d / "state.json", {"current_phase": 6})
    if with_inventory:
        _write_json(d / "inventory.json", REAL_INVENTORY)
    return d


# ---------------------------------------------------------------------------
# Chapter scanning
# ---------------------------------------------------------------------------


def test_scan_chapter_counts_outside_fences_only(tmp_path: Path) -> None:
    m = mod.ChapterMetric("01-overview.md")
    p = tmp_path / "01-overview.md"
    p.write_text(GOOD_CHAPTER, encoding="utf-8")
    mod.scan_chapter(p, m)

    # Fence content excluded: only the outside labels count.
    # NOTE: "🟢 VERIFIED: ..." lines count twice (emoji + word markers),
    # matching coverage-check.py's counting convention.
    assert m.verified == 4
    assert m.inferred == 2
    assert m.assumed == 2
    assert m.refs == 2
    assert m.unresolved == 0
    # Body lines: 8 non-empty non-fence lines in GOOD_CHAPTER.
    assert m.body_lines == 8
    assert m.unclosed_fence is False


def test_scan_chapter_counts_unresolved_and_conf_comments(tmp_path: Path) -> None:
    m = mod.ChapterMetric("02-data-model.md")
    p = tmp_path / "02-data-model.md"
    p.write_text(BAD_CHAPTER, encoding="utf-8")
    mod.scan_chapter(p, m)

    # CONFIDENCE: verified comment counts as verified.
    assert m.verified == 1
    assert m.assumed == 4  # 🔴 ASSUMED x2 lines → 2 markers each (emoji + word)
    assert m.refs == 3
    assert m.unresolved == 2  # BLOCKED + ASK SME


def test_scan_chapter_word_boundary_no_false_positive(tmp_path: Path) -> None:
    m = mod.ChapterMetric("w.md")
    p = tmp_path / "w.md"
    p.write_text(
        "# W\n\nUNVERIFIED claims and UNASSUMED statements should not count.\n"
        "VERIFIED does count.\n",
        encoding="utf-8",
    )
    mod.scan_chapter(p, m)
    assert m.verified == 1  # only the standalone VERIFIED
    assert m.assumed == 0


def test_scan_chapter_unclosed_fence_flag(tmp_path: Path) -> None:
    m = mod.ChapterMetric("u.md")
    p = tmp_path / "u.md"
    p.write_text(
        "# U\n\nbefore\n```\nafter fence start\n",
        encoding="utf-8",
    )
    mod.scan_chapter(p, m)
    assert m.unclosed_fence is True


def test_scan_chapter_non_utf8_skipped(tmp_path: Path) -> None:
    m = mod.ChapterMetric("bin.md")
    p = tmp_path / "bin.md"
    p.write_bytes(b"\xff\xfe\x00")
    mod.scan_chapter(p, m)  # must not raise; garbage decodes to replacement chars
    assert m.verified == 0
    assert m.assumed == 0
    assert m.refs == 0


def test_chapter_score_penalizes_assumed_and_thin_body() -> None:
    assumed_heavy = mod.ChapterMetric("a.md")
    assumed_heavy.body_lines = 50
    assumed_heavy.refs = 5
    assumed_heavy.assumed = 5
    # 5/5 assumed → ratio 1.0 → -50
    assert assumed_heavy.score() == 100 - 50 + min(5 / 50 * 200, 10) == 60

    thin = mod.ChapterMetric("thin.md")
    thin.body_lines = 5
    thin.refs = 0
    thin.verified = 1
    assert thin.score() == 100 - 20 == 80

    unresolved = mod.ChapterMetric("u.md")
    unresolved.body_lines = 50
    unresolved.refs = 5
    unresolved.verified = 5
    unresolved.unresolved = 3
    assert unresolved.score() == 100 - 3 * 5 + min(5 / 50 * 200, 10) == 95


def test_chapter_score_no_labels_treated_as_assumed() -> None:
    # A chapter with zero confidence labels must NOT score 100: no evidence
    # is treated as assumed (Issue #268 code review).
    no_labels = mod.ChapterMetric("n.md")
    no_labels.body_lines = 50
    no_labels.refs = 3
    assert no_labels.assumed_ratio == 1.0
    assert no_labels.score() == 100 - 50 + min(3 / 50 * 200, 10) == 60


def test_chapter_score_ref_bonus_capped() -> None:
    m = mod.ChapterMetric("r.md")
    m.body_lines = 10
    m.refs = 100  # density 10 → bonus capped at 10
    m.verified = 10
    assert m.score() == 100  # 100 + 10 clamped to 100
    assert m.score() <= 100


def test_chapter_score_clamped_low() -> None:
    m = mod.ChapterMetric("x.md")
    m.body_lines = 100
    m.refs = 0
    m.assumed = 10  # ratio 1.0 → -50
    m.unresolved = 10  # -50
    assert m.score() == 0  # 100 - 50 - 50 = 0


# ---------------------------------------------------------------------------
# Overall score & rating
# ---------------------------------------------------------------------------


def test_overall_score_weighted_average() -> None:
    chapters = [mod.ChapterMetric("a.md") for _ in range(2)]
    for c in chapters:
        c.body_lines = 50
        c.refs = 5
        c.verified = 5
    score = mod.overall_score(
        chapters,
        coverage_rate=100.0,
        mece_rate=1.0,
        assumed_ratio=0.0,
        questions={"total": 10, "open": 0, "open_ratio": 0.0},
    )
    # All metrics present → full-weight average of 100s.
    assert score == 100.0


def test_overall_score_renormalizes_missing_metrics() -> None:
    chapters = [mod.ChapterMetric("a.md")]
    chapters[0].body_lines = 50
    chapters[0].refs = 5
    chapters[0].verified = 5
    score = mod.overall_score(
        chapters,
        coverage_rate=None,  # coverage-check failed → N/A
        mece_rate=None,      # trace.json missing → N/A
        assumed_ratio=0.0,
        questions={"total": None, "open": None, "open_ratio": None},
    )
    # Only chapters (w=0.15) contributes; renormalized → chapters mean (100).
    assert score == 100.0


def test_overall_score_zero_when_assumed_ratio_is_one() -> None:
    # assumed_score is always a metric; with ratio 1.0 it contributes 0.
    assert mod.overall_score([], None, None, 1.0, {"total": None}) == 0.0


def test_rating_boundaries() -> None:
    assert mod.rating_for(90.0)[0] == "A"
    assert mod.rating_for(89.9)[0] == "B"
    assert mod.rating_for(75.0)[0] == "B"
    assert mod.rating_for(74.9)[0] == "C"
    assert mod.rating_for(60.0)[0] == "C"
    assert mod.rating_for(59.9)[0] == "D"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_markdown_contains_required_sections(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    chapters = mod.collect_chapters(d, d)
    md = mod.render_markdown(
        d,
        chapters,
        {"coverage_rate": None, "gate_failures": None, "available": False},
        None,
        mod.load_drift(d, d),
        mod.load_questions(d),
        mod.load_phase(d),
        72.0,
        "C",
        "精緻化が必要",
        0.3,
    )
    for section in (
        "## Summary",
        "## Per-chapter scorecard",
        "## Needs refinement (Phase 5 suggested)",
        "## Gate failures (from coverage-check)",
        "02-data-model.md",
    ):
        assert section in md
    assert "N/A" in md  # missing coverage displayed as N/A


def test_render_markdown_escapes_table_injection(tmp_path: Path) -> None:
    m = mod.ChapterMetric("01-overview.md|**SPOOF**")
    m.body_lines = 50
    m.refs = 5
    m.verified = 5
    md = mod.render_markdown(
        tmp_path,
        [m],
        {"coverage_rate": None, "gate_failures": ["injected|row"], "available": True},
        None,
        {"changed_files": None, "affected_sections": None, "new_uncovered": None},
        {"total": None, "open": None, "open_ratio": None},
        None,
        80.0,
        "B",
        "軽微な精緻化を推奨",
        0.3,
    )
    assert "| **SPOOF**" not in md
    assert "injected\\|row" in md
    assert "01-overview.md\\|" in md


def test_render_json_shape(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    chapters = mod.collect_chapters(d, d)
    js = mod.render_json(
        d,
        chapters,
        {"coverage_rate": 85.0, "gate_failures": ["x"], "available": True},
        0.8,
        mod.load_drift(d, d),
        mod.load_questions(d),
        mod.load_phase(d),
        72,
        "C",
        "精緻化が必要",
        0.3,
    )
    doc = json.loads(js)
    assert doc["schema_version"] == "1.0.0"
    assert doc["overall_score"] == 72
    assert doc["rating"] == "C"
    assert "chapters" in doc and len(doc["chapters"]) == 2
    assert doc["summary"]["open_questions"] == 2
    assert doc["summary"]["questions_total"] == 3
    assert doc["summary"]["drift_changed_files"] == 2


def test_needs_refinement_threshold(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    chapters = mod.collect_chapters(d, d)
    # 02-data-model.md: assumed=4, total=5 → ratio 0.8 > 0.3
    offenders = mod.needs_refinement(chapters, 0.3)
    assert [c.file for c in offenders] == ["02-data-model.md"]
    # Raise threshold above 0.8 → no offenders.
    assert mod.needs_refinement(chapters, 0.9) == []


# ---------------------------------------------------------------------------
# Loaders (real producer shapes)
# ---------------------------------------------------------------------------


def test_load_trace_derives_mece_from_real_schema(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    # Real build-trace shape has NO mece_coverage_rate key.
    assert "mece_coverage_rate" not in REAL_TRACE
    result = mod.load_trace(d, d)
    # 8 covered / max(10 - 1, 1) = 8/9 ≈ 0.889
    assert result["mece_coverage_rate"] is not None
    assert abs(result["mece_coverage_rate"] - 8 / 9) < 1e-9


def test_load_trace_reads_from_output_dir_too(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    out = tmp_path / "out"
    _write_json(out / "trace.json", REAL_TRACE)
    result = mod.load_trace(d, out)
    assert result["mece_coverage_rate"] is not None


def test_load_trace_rejects_bool(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    _write_json(d / "trace.json", {"source_units_total": 10, "source_units_covered": True, "source_units_excluded": 1})
    assert mod.load_trace(d, d)["mece_coverage_rate"] is None


def test_load_drift_reads_output_dir_first(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    out = tmp_path / "out"
    _write_json(out / "drift-report.json", {"summary": {"changed_files": 7, "affected_spec_sections": 3, "new_uncovered_sources": 1}})
    result = mod.load_drift(d, out)
    assert result["changed_files"] == 7
    assert result["affected_sections"] == 3


def test_load_json_rejects_nan(tmp_path: Path) -> None:
    d = tmp_path / "x.json"
    d.write_text('{"rate": NaN}', encoding="utf-8")
    with pytest.raises(ValueError):
        mod.load_json(d)


# ---------------------------------------------------------------------------
# CLI (subprocess)
# ---------------------------------------------------------------------------


def run_cli(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    d = make_specback(tmp_path)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(d), *extra],
        capture_output=True,
        text=True,
    )


def test_cli_exit_0_and_writes_report(tmp_path: Path) -> None:
    proc = run_cli(tmp_path)
    assert proc.returncode == 0, proc.stderr
    report = tmp_path / ".specback" / "health-report.md"
    assert report.is_file()
    assert "# Spec Health Report" in report.read_text(encoding="utf-8")


def test_cli_default_invocation_coverage_available(tmp_path: Path) -> None:
    # Documented default (no --output-dir) must resolve coverage against
    # drafts/, not the empty specback root (Issue #268 code review 🔴2).
    proc = run_cli(tmp_path, "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(
        (tmp_path / ".specback" / "health-report.json").read_text(encoding="utf-8")
    )
    assert doc["summary"]["coverage_rate"] is not None
    assert doc["coverage_available"] is True


def test_cli_explicit_output_dir(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--specback-dir",
            str(d),
            "--output-dir",
            str(out),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "health-report.md").is_file()
    doc = json.loads((out / "health-report.json").read_text(encoding="utf-8"))
    # Final-dir invocation: chapters come from drafts/ only (out is empty).
    assert doc["summary"]["chapters_scanned"] == 2


def test_cli_json_flag(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--json")
    assert proc.returncode == 0, proc.stderr
    json_path = tmp_path / ".specback" / "health-report.json"
    assert json_path.is_file()
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "1.0.0"


def test_cli_exit_1_when_specback_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(missing)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_cli_exit_1_when_no_chapters(tmp_path: Path) -> None:
    d = tmp_path / ".specback"
    d.mkdir(parents=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(d)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "no chapter files" in proc.stderr


def test_cli_min_health_score_gate(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--min-health-score", "99")
    assert proc.returncode == 2
    assert "GATE FAILED" in proc.stderr


def test_cli_min_health_score_pass(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--min-health-score", "1")
    assert proc.returncode == 0, proc.stderr


def test_cli_min_health_score_nan_rejected(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--min-health-score", "nan")
    assert proc.returncode == 1
    assert "finite" in proc.stderr


def test_cli_assumed_threshold_changes_needs_refinement(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--json", "--assumed-ratio-threshold", "0.9")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(
        (tmp_path / ".specback" / "health-report.json").read_text(encoding="utf-8")
    )
    assert doc["needs_refinement"] == []

    proc = run_cli(tmp_path, "--json", "--assumed-ratio-threshold", "0.3")
    doc = json.loads(
        (tmp_path / ".specback" / "health-report.json").read_text(encoding="utf-8")
    )
    assert doc["needs_refinement"] == ["02-data-model.md"]


# ---------------------------------------------------------------------------
# Security hardening (agency review follow-up)
# ---------------------------------------------------------------------------


def test_cli_rejects_symlinked_report(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL", encoding="utf-8")
    (d / "health-report.md").symlink_to(victim)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(d)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "symlink" in proc.stderr
    assert victim.read_text(encoding="utf-8") == "ORIGINAL"  # untouched


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo not available")
def test_cli_fifo_chapter_does_not_hang(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    os.mkfifo(d / "drafts" / "pipe.md")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(d)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_cli_non_utf8_chapter_skipped(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    (d / "drafts" / "03-binary.md").write_bytes(b"\xff\xfe\x00")
    proc = run_cli(tmp_path, "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(
        (tmp_path / ".specback" / "health-report.json").read_text(encoding="utf-8")
    )
    # The binary chapter decodes to replacement chars: no labels, no crash.
    binary = [c for c in doc["chapters"] if c["file"] == "03-binary.md"]
    assert binary and binary[0]["verified"] == 0 and binary[0]["assumed"] == 0


def test_cli_reserved_files_excluded(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    (d / "drafts" / "00-metadata.md").write_text("# Meta\n\nno labels here\n", encoding="utf-8")
    (d / "drafts" / "traceability.md").write_text("| A | B |\n", encoding="utf-8")
    proc = run_cli(tmp_path, "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(
        (tmp_path / ".specback" / "health-report.json").read_text(encoding="utf-8")
    )
    names = [c["file"] for c in doc["chapters"]]
    assert "00-metadata.md" not in names
    assert "traceability.md" not in names


def test_parse_args_specback_dir_is_path() -> None:
    args = mod.parse_args(["--specback-dir", "custom-sb"])
    assert args.specback_dir == Path("custom-sb")
    args_default = mod.parse_args([])
    assert args_default.specback_dir == Path(".specback")
