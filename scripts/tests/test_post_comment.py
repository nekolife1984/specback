"""Tests for the specback-drift PR comment generator (Issue #266).

Covers:
- pass + no changes -> no comment (exit 0, no file written)
- fail verdict -> comment with FAIL status and summary numbers
- warn verdict -> comment with WARN status
- warnings are rendered as bullets
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent \
    / ".github" / "actions" / "specback-drift" / "post-comment.py"

_spec = importlib.util.spec_from_file_location("post_comment_core", SCRIPT)
assert _spec is not None and _spec.loader is not None
pc = importlib.util.module_from_spec(_spec)
sys.modules["post_comment_core"] = pc
_spec.loader.exec_module(pc)


def _write_report(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "report.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _report(verdict: str = "pass", changed: int = 0, affected: int = 0,
            new_uncovered: int = 0, deleted: int = 0, orphaned: int = 0,
            warnings: list[str] | None = None) -> dict:
    return {
        "schema_version": "0.1.0",
        "verdict": verdict,
        "drift": {"summary": {
            "changed_files": changed,
            "affected_spec_sections": affected,
            "new_uncovered_sources": new_uncovered,
            "deleted_sources_with_refs": deleted,
        }},
        "fix_refs": {"orphaned": orphaned},
        "warnings": warnings or [],
    }


def test_no_comment_on_clean_pass(tmp_path):
    report = _write_report(tmp_path, _report())
    comment = tmp_path / "comment.md"
    assert pc.main([str(report), str(comment)]) == 0
    assert not comment.exists()


def test_no_comment_on_pass_with_changes(tmp_path):
    """Changed files alone (no drift, no warnings) -> no comment."""
    report = _write_report(tmp_path, _report(changed=3))
    comment = tmp_path / "comment.md"
    assert pc.main([str(report), str(comment)]) == 0
    assert not comment.exists()


def test_comment_on_fail(tmp_path):
    report = _write_report(tmp_path, _report(
        verdict="fail", changed=3, affected=2, new_uncovered=1, deleted=1,
    ))
    comment = tmp_path / "comment.md"
    assert pc.main([str(report), str(comment)]) == 0
    assert comment.exists()
    body = comment.read_text(encoding="utf-8")
    assert "🔴 FAIL" in body
    assert "**Changed files**: 3" in body
    assert "**Affected spec sections**: 2" in body
    assert "**New uncovered sources**: 1" in body
    assert "**Deleted sources with refs**: 1" in body


def test_comment_on_warn_with_orphaned_refs(tmp_path):
    report = _write_report(tmp_path, _report(
        verdict="warn", changed=1, orphaned=2,
        warnings=["2 orphaned REF(s) found — run fix-refs.py --apply"],
    ))
    comment = tmp_path / "comment.md"
    assert pc.main([str(report), str(comment)]) == 0
    assert comment.exists()
    body = comment.read_text(encoding="utf-8")
    assert "🟡 WARN" in body
    assert "**Orphaned REFs**: 2" in body
    assert "- 2 orphaned REF(s) found" in body


def test_comment_on_warn_due_to_warnings_only(tmp_path):
    """Warnings without changes still produce a comment."""
    report = _write_report(tmp_path, _report(
        verdict="warn", changed=0,
        warnings=["spec artifacts missing (source-map.json) — run specback "
                  "to generate the spec first; drift gate skipped"],
    ))
    comment = tmp_path / "comment.md"
    assert pc.main([str(report), str(comment)]) == 0
    assert comment.exists()
    body = comment.read_text(encoding="utf-8")
    assert "🟡 WARN" in body
    assert "spec artifacts missing" in body


def test_missing_report_does_not_crash(tmp_path):
    comment = tmp_path / "comment.md"
    assert pc.main([str(tmp_path / "nope.json"), str(comment)]) == 0
    assert not comment.exists()
