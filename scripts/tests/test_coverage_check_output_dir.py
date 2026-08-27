"""Tests for coverage-check.py --output-dir argument and --target-dir-for-required fallback."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "coverage-check.py"


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


def test_target_dir_no_longer_restricted_to_choices():
    """--target-dir-for-required accepts arbitrary paths (not just 'drafts'/'final')."""
    # Passing an arbitrary path with --help should not error
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--target-dir-for-required", ".specback/drafts", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--target-dir-for-required" in result.stdout

    # Also verify "drafts" and "final" still work
    result_drafts = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--target-dir-for-required", "drafts", "--help"],
        capture_output=True, text=True,
    )
    assert result_drafts.returncode == 0

    result_final = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--target-dir-for-required", "final", "--help"],
        capture_output=True, text=True,
    )
    assert result_final.returncode == 0


def test_help_shows_standalone_path_description():
    """--help mentions standalone path support."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "standalone path" in result.stdout


def test_target_dir_fallback_standalone_path(tmp_path):
    """
    When output_dir / target_dir_name doesn't exist but target_dir_name
    as a standalone path does, the script should use the standalone path.
    """
    # Create a minimal .specback structure
    specback_dir = tmp_path / ".specback"
    specback_dir.mkdir()
    final_dir = specback_dir / "final"
    final_dir.mkdir()

    # Minimal inventory.json
    inventory = {"units": []}
    (specback_dir / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    # Minimal trace.json to avoid the missing-trace gate failure
    trace = {"source_units_total": 0, "source_units_covered": 0,
             "source_units_excluded": 0, "source_units_uncovered": 0}
    (specback_dir / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    # Place a chapter file and required files under the standalone path
    chapter = final_dir / "01-overview.md"
    chapter.write_text("# Overview\n\nContent here.\n", encoding="utf-8")
    (final_dir / "00-metadata.md").write_text("# Metadata\n", encoding="utf-8")
    (final_dir / "99-unresolved.md").write_text("# Unresolved\n", encoding="utf-8")
    (final_dir / "traceability.md").write_text("# Traceability\n", encoding="utf-8")

    # Now run with --output-dir pointing to a different (empty) directory
    # and --target-dir-for-required pointing to the actual location
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(specback_dir),
         "--output-dir", str(other_dir),
         "--target-dir-for-required", str(final_dir),
         "--output-format", "json",
         "--min-inventory", "0",
         "--min-questions", "0",
         "--min-covered-by-fill", "0",
         "--min-mece-coverage", "0",
         "--min-refs-per-chapter", "0",
         "--min-lines-per-chapter", "0",
         "--min-code-blocks-per-chapter", "0",
         "--min-mermaid-per-chapter", "0",
         "--require-min-body-lines-for-reserved", "0",
         "--no-forbid-mermaid-styling"],
        capture_output=True, text=True,
    )
    # Exit 0 = pass (fallback found the files at standalone path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)
    assert report["missing_required"] == []
    assert report["gate_failures"] == []
    filenames = [m["file"] for m in report["chapter_metrics"]]
    assert "01-overview.md" in filenames


def test_target_dir_fallback_skipped_when_normal_path_exists(tmp_path):
    """
    When output_dir / target_dir_name exists, the fallback is NOT triggered.
    """
    specback_dir = tmp_path / ".specback"
    specback_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    drafts_dir = output_dir / "drafts"
    drafts_dir.mkdir()

    inventory = {"units": []}
    (specback_dir / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    # Minimal trace.json to avoid the missing-trace gate failure
    trace = {"source_units_total": 0, "source_units_covered": 0,
             "source_units_excluded": 0, "source_units_uncovered": 0}
    (specback_dir / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    # Place chapter and required files under the normal resolved path
    chapter = drafts_dir / "01-overview.md"
    chapter.write_text("# Overview\n\nContent here.\n", encoding="utf-8")
    (drafts_dir / "00-metadata.md").write_text("# Metadata\n", encoding="utf-8")
    (drafts_dir / "99-unresolved.md").write_text("# Unresolved\n", encoding="utf-8")
    (drafts_dir / "traceability.md").write_text("# Traceability\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(specback_dir),
         "--output-dir", str(output_dir),
         "--target-dir-for-required", "drafts",
         "--output-format", "json",
         "--min-inventory", "0",
         "--min-questions", "0",
         "--min-covered-by-fill", "0",
         "--min-mece-coverage", "0",
         "--min-refs-per-chapter", "0",
         "--min-lines-per-chapter", "0",
         "--min-code-blocks-per-chapter", "0",
         "--min-mermaid-per-chapter", "0",
         "--require-min-body-lines-for-reserved", "0",
         "--no-forbid-mermaid-styling"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)
    assert report["missing_required"] == []
    assert report["gate_failures"] == []
    filenames = [m["file"] for m in report["chapter_metrics"]]
    assert "01-overview.md" in filenames


def test_generated_reports_not_quality_gated(tmp_path):
    """
    SB-06: drift-report.md / health-report.md must NOT be treated as spec
    chapters — no chapter metric, no naming violation, no quality-gate failure.
    """
    specback_dir = tmp_path / ".specback"
    specback_dir.mkdir()
    target_dir = specback_dir / "final"
    target_dir.mkdir()

    inventory = {"units": []}
    (specback_dir / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    # Minimal trace.json to avoid the missing-trace gate failure
    trace = {"source_units_total": 0, "source_units_covered": 0,
             "source_units_excluded": 0, "source_units_uncovered": 0}
    (specback_dir / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    # A valid standard chapter that MEETS the strict quality thresholds below:
    # ≥200 effective lines, ≥10 REFs, ≥3 code blocks, ≥1 Mermaid.
    lines = ["# Overview", ""]
    for i in range(60):
        lines.append(f"Section {i} describes behaviour {i}.")
        lines.append("<!-- REF: src/app.py:10-20 -->")
        lines.append("Some detailed prose that explains the behaviour in context.")
        lines.append("Another sentence of documentation prose to add body length.")
        lines.append("")
    lines.append("```python")
    lines.append("def sample():")
    lines.append("    return True")
    lines.append("```")
    lines.append("")
    lines.append("```bash")
    lines.append("$ echo hello")
    lines.append("```")
    lines.append("")
    lines.append("```text")
    lines.append("plain text block")
    lines.append("```")
    lines.append("")
    lines.append("```mermaid")
    lines.append("graph TD")
    lines.append("    A-->B")
    lines.append("```")
    lines.append("")
    (target_dir / "01-overview.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (target_dir / "00-metadata.md").write_text("# Metadata\n", encoding="utf-8")
    (target_dir / "99-unresolved.md").write_text("# Unresolved\n", encoding="utf-8")
    (target_dir / "traceability.md").write_text("# Traceability\n", encoding="utf-8")

    # Specback-generated reports that do NOT satisfy chapter quality gates.
    (target_dir / "drift-report.md").write_text(
        "| file | SRC-ID | impact |\n|---|---|---|\n| a.py | SRC-1 | high |\n",
        encoding="utf-8")
    (target_dir / "health-report.md").write_text(
        "# Health\n\nSome report body without REFs or Mermaid.\n",
        encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--specback-dir", str(specback_dir),
         "--output-dir", str(tmp_path / "output"),
         "--target-dir-for-required", str(target_dir),
         "--output-format", "json",
         "--min-inventory", "0",
         "--min-questions", "0",
         "--min-covered-by-fill", "0",
         "--min-mece-coverage", "0",
         "--min-refs-per-chapter", "10",
         "--min-lines-per-chapter", "200",
         "--min-code-blocks-per-chapter", "3",
         "--min-mermaid-per-chapter", "1",
         "--require-min-body-lines-for-reserved", "0",
         "--no-forbid-mermaid-styling"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)

    # Reports are excluded from chapter metrics entirely.
    filenames = [m["file"] for m in report["chapter_metrics"]]
    assert "01-overview.md" in filenames
    assert "drift-report.md" not in filenames
    assert "health-report.md" not in filenames

    # No naming violation for the reports, and no quality-gate failure refers to them.
    assert all("drift-report" not in w and "health-report" not in w
               for w in report["naming_warnings"])
    assert all("drift-report" not in f and "health-report" not in f
               for f in report["gate_failures"])
