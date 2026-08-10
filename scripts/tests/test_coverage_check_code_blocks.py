"""
Tests for coverage-check.py --code-block-line-weight feature (Issue #111).

Verifies that code-block lines are counted at a configurable weight
(default 0.5) toward the body-lines threshold.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "coverage-check.py"


def _minimal_specback(tmp_path: Path, chapter_content: str, chapter_name: str = "01-overview.md") -> Path:
    """Create a minimal .specback directory with one chapter file."""
    specback_dir = tmp_path / ".specback"
    specback_dir.mkdir()
    final_dir = specback_dir / "final"
    final_dir.mkdir()

    inventory: dict[str, Any] = {"units": []}
    (specback_dir / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    trace = {
        "source_units_total": 0, "source_units_covered": 0,
        "source_units_excluded": 0, "source_units_uncovered": 0,
    }
    (specback_dir / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (specback_dir / "goal.json").write_text(
        json.dumps({"template": "default"}), encoding="utf-8",
    )

    (final_dir / chapter_name).write_text(chapter_content, encoding="utf-8")
    (final_dir / "00-metadata.md").write_text("# Metadata\n", encoding="utf-8")
    (final_dir / "99-unresolved.md").write_text("# Unresolved\n", encoding="utf-8")
    (final_dir / "traceability.md").write_text("# Traceability\n", encoding="utf-8")
    return specback_dir


def _run_check(specback_dir: Path, **overrides) -> dict:
    """Run coverage-check.py with JSON output and the given overrides.

    Returns the parsed JSON report regardless of exit code (exit 1 is
    expected when body lines fall below the threshold).
    """
    defaults = {
        "--min-inventory": "0",
        "--min-questions": "0",
        "--min-covered-by-fill": "0",
        "--min-mece-coverage": "0",
        "--min-refs-per-chapter": "0",
        "--min-lines-per-chapter": "200",
        "--min-code-blocks-per-chapter": "0",
        "--min-mermaid-per-chapter": "0",
        "--min-sources-read-per-chapter": "0",
    }
    defaults.update(overrides)

    cmd = [
        sys.executable, str(SCRIPT),
        "--specback-dir", str(specback_dir),
        "--output-format", "json",
    ]
    for key, val in defaults.items():
        cmd.extend([key, str(val)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    # Exit 1 is normal when body lines < threshold; just return the report
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"Failed to parse JSON output. exit={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )


def _chapter_by_name(report: dict, name: str) -> dict:
    """Find a chapter metric by filename."""
    for m in report["chapter_metrics"]:
        if m["file"] == name:
            return m
    raise AssertionError(f"Chapter {name!r} not found in {[m['file'] for m in report['chapter_metrics']]}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_help_shows_code_block_line_weight():
    """--help includes the --code-block-line-weight flag."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--code-block-line-weight" in result.stdout


def test_json_contains_mece_passed_strict(tmp_path):
    """--output-format json must include mece_passed_strict (Issue #256).

    gates.py reads mece_passed_strict to decide the strict MECE gate;
    without the key it silently falls back to rate >= 0.7.
    """
    content = "# Overview\n\nSome text.\n"
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir, **{"--min-lines-per-chapter": "0"})
    assert "mece_passed_strict" in report, (
        f"mece_passed_strict missing from JSON: {list(report.keys())}"
    )
    assert isinstance(report["mece_passed_strict"], bool)


def test_code_block_lines_counted_at_default_weight(tmp_path):
    """
    A chapter with 6 non-blank code-block lines should get +3 effective
    lines at the default weight of 0.5.
    """
    content = """# Overview

Some text.

```python
def hello():
    print("hello")
    print("world")
    return 42

def goodbye():
    print("bye")
```

More text.
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)

    m = _chapter_by_name(report, "01-overview.md")
    assert m["code_block_lines"] == 6, f"Expected 6 code_block_lines, got {m['code_block_lines']}"
    assert m["body_lines"] == 3, f"Expected 3 body_lines, got {m['body_lines']}"
    # effective = 3 + int(6 * 0.5) = 6
    assert m["failures"]
    assert "code-block-adjusted: 6" in m["failures"][0]


def test_code_block_lines_weight_1_0_counts_fully(tmp_path):
    """
    With --code-block-line-weight=1.0, every non-blank code-block line
    counts as a full body line.
    """
    content = """# Overview

A short spec with code.

```python
line1 = 1
line2 = 2
```
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir, **{"--code-block-line-weight": "1.0"})

    m = _chapter_by_name(report, "01-overview.md")
    assert m["code_block_lines"] == 2, f"Expected 2, got {m['code_block_lines']}"
    assert m["body_lines"] == 2  # "# Overview", "A short spec with code."
    # effective = 2 + int(2 * 1.0) = 4
    assert "code-block-adjusted: 4" in m["failures"][0]


def test_code_block_lines_weight_0_0_excludes_code(tmp_path):
    """
    With --code-block-line-weight=0.0, code-block lines are ignored entirely
    (original behaviour). effective == body_lines.
    """
    content = """# Overview

A short spec.

```python
hidden = "content"
another = "line"
```
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir, **{"--code-block-line-weight": "0.0"})

    m = _chapter_by_name(report, "01-overview.md")
    assert m["code_block_lines"] == 2, f"Expected 2, got {m['code_block_lines']}"
    assert m["body_lines"] == 2
    # effective = 2 + int(2 * 0.0) = 2 → same as body_lines
    assert "code-block-adjusted: 2" in m["failures"][0]


def test_code_block_lines_does_not_affect_other_metrics(tmp_path):
    """
    Code-block counting should not affect refs, code_blocks, or mermaid_blocks counts.
    """
    content = """# Chapter

Some text. <!-- REF: file:1-5 -->

```python
import os
```

```mermaid
graph TD; A-->B;
```
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)

    m = _chapter_by_name(report, "01-overview.md")
    assert m["code_block_lines"] == 2, f"Expected 2, got {m['code_block_lines']}"
    assert m["refs"] == 1
    assert m["code_blocks"] == 1
    assert m["mermaid_blocks"] == 1


def test_no_code_blocks_zero_code_block_lines(tmp_path):
    """When there are no code blocks, code_block_lines is 0."""
    content = """# Chapter

Just plain text.

* item 1
* item 2
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)

    m = _chapter_by_name(report, "01-overview.md")
    assert m["code_block_lines"] == 0
    assert m["code_blocks"] == 0
    assert m["mermaid_blocks"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# SRC-ID REF counting tests (Issue #224)
# ═══════════════════════════════════════════════════════════════════════════


def test_src_id_refs_counted_in_total(tmp_path):
    """SRC-ID format (<!-- REF: SRC-NNNN -->) should count as REFs."""
    content = """# Chapter

Some text. <!-- REF: SRC-0001 -->

More text. <!-- REF: SRC-0142 -->
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)
    m = _chapter_by_name(report, "01-overview.md")
    assert m["refs"] == 2, f"Expected 2 refs (both SRC-ID), got {m['refs']}"


def test_mixed_src_id_and_path_line_refs(tmp_path):
    """Both SRC-ID and path:line refs should be counted together."""
    content = """# Chapter

Text. <!-- REF: SRC-0001 -->
Text. <!-- REF: src/errors.py:1-50 -->
Text. <!-- REF: SRC-0142 -->
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)
    m = _chapter_by_name(report, "01-overview.md")
    assert m["refs"] == 3, f"Expected 3 refs (2 SRC-ID + 1 path:line), got {m['refs']}"


def test_src_id_refs_in_code_fenced_block_ignored(tmp_path):
    """REF markers inside code fences should not be counted."""
    content = """# Chapter

Some real text. <!-- REF: SRC-0001 -->

```python
<!-- REF: SRC-0002 -->
<! -- REF: SRC-0003 -->
```

More text. <!-- REF: SRC-0142 -->
"""
    specback_dir = _minimal_specback(tmp_path, content)
    report = _run_check(specback_dir)
    m = _chapter_by_name(report, "01-overview.md")
    assert m["refs"] == 2, f"Expected 2 refs (outside code fence), got {m['refs']}"
