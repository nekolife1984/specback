"""Smoke tests for fix-refs.py (Phase 7b — REF Auto-Fix).

Tests include SRC-ID support (Issue #224): verification that SRC-format
refs are correctly detected and skipped by the auto-fix logic.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "fix-refs.py"

# Import the regex patterns from the script for unit testing
# We re-import the module each time to get fresh constants
def _import_fix_refs():
    """Import fix-refs.py as a module to access its constants."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fix_refs_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CLI smoke tests
# ═══════════════════════════════════════════════════════════════════════════


def test_help_includes_specback_dir():
    result = _run("--help")
    assert result.returncode == 0
    assert "--specback-dir" in result.stdout


def test_help_includes_output_dir():
    result = _run("--help")
    assert result.returncode == 0
    assert "--output-dir" in result.stdout


def test_help_includes_apply():
    result = _run("--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout


def test_help_includes_check():
    result = _run("--help")
    assert result.returncode == 0
    assert "--check" in result.stdout


def test_args_with_output_dir():
    """--output-dir combined with --help doesn't error."""
    result = _run("--output-dir", "/tmp/x", "--help")
    assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════
# SRC-ID regex tests (Issue #224)
# ═══════════════════════════════════════════════════════════════════════════


class TestSrcRefRe:
    """Verify SRC_REF_RE correctly matches <!-- REF: SRC-NNNN -->."""

    def test_matches_src_id(self):
        mod = _import_fix_refs()
        matches = mod.SRC_REF_RE.findall("<!-- REF: SRC-0142 -->")
        assert matches == ["SRC-0142"]

    def test_matches_src_id_with_spaces(self):
        mod = _import_fix_refs()
        matches = mod.SRC_REF_RE.findall("<!-- REF:   SRC-0001   -->")
        assert matches == ["SRC-0001"]

    def test_does_not_match_path_line(self):
        mod = _import_fix_refs()
        matches = mod.SRC_REF_RE.findall(
            "<!-- REF: src/errors.py:1-50 -->"
        )
        assert matches == []

    def test_does_not_match_invalid_format(self):
        mod = _import_fix_refs()
        matches = mod.SRC_REF_RE.findall("<!-- REF: SRC-XXX -->")
        assert matches == []

    def test_does_not_match_plain_path(self):
        mod = _import_fix_refs()
        matches = mod.SRC_REF_RE.findall("<!-- REF: app/models/user.rb -->")
        assert matches == []


class TestFindRefsInFileSrcId:
    """Verify SRC-ID refs are detected with is_src_id=True."""

    def test_src_id_ref_detected(self, tmp_path):
        mod = _import_fix_refs()
        spec_file = tmp_path / "01-overview.md"
        spec_file.write_text(
            "<!-- REF: SRC-0142 -->\n", encoding="utf-8"
        )
        refs = mod.find_refs_in_file(spec_file)
        assert len(refs) == 1
        assert refs[0]["is_src_id"] is True
        assert refs[0]["ref_path"] == "SRC-0142"

    def test_src_id_and_path_line_both_detected(self, tmp_path):
        mod = _import_fix_refs()
        spec_file = tmp_path / "02-data.md"
        spec_file.write_text(
            "<!-- REF: SRC-0142 -->\n"
            "<!-- REF: app/models/user.rb:42 -->\n",
            encoding="utf-8",
        )
        refs = mod.find_refs_in_file(spec_file)
        assert len(refs) == 2
        assert refs[0]["is_src_id"] is True
        assert refs[0]["ref_path"] == "SRC-0142"
        assert refs[1]["is_src_id"] is False
        assert refs[1]["ref_path"] == "app/models/user.rb"


class TestSrcRefReInRefRe:
    """Verify REF_RE does NOT match SRC-ID format (no false overlap)."""

    def test_ref_re_does_not_match_src_id(self):
        mod = _import_fix_refs()
        matches = mod.REF_RE.findall("<!-- REF: SRC-0142 -->")
        assert matches == []


# ═══════════════════════════════════════════════════════════════════════════
# SRC-ID migration (--migrate-srcid)
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadSourceMap:
    """load_source_map() reads source-map.json and indexes by path."""

    def test_missing_file_returns_empty(self, tmp_path):
        mod = _import_fix_refs()
        assert mod.load_source_map(tmp_path) == {}

    def test_indexes_units_by_path(self, tmp_path):
        mod = _import_fix_refs()
        (tmp_path / "source-map.json").write_text(json.dumps({
            "units": [
                {"id": "SRC-0001", "path": "src/a.py", "line_range": [1, 10]},
                {"id": "SRC-0002", "path": "src/b.py", "line_range": [5, 20]},
            ]
        }), encoding="utf-8")
        by_path = mod.load_source_map(tmp_path)
        assert set(by_path.keys()) == {"src/a.py", "src/b.py"}
        assert by_path["src/a.py"][0]["id"] == "SRC-0001"

    def test_invalid_json_returns_empty(self, tmp_path):
        mod = _import_fix_refs()
        (tmp_path / "source-map.json").write_text("{not json", encoding="utf-8")
        assert mod.load_source_map(tmp_path) == {}


class TestClassifyMigration:
    """classify_migration() decides which path:line REFs can be SRC-ID'd."""

    def _units(self):
        return {
            "src/app.py": [
                {"id": "SRC-0001", "path": "src/app.py", "line_range": [10, 42]},
            ]
        }

    def test_src_id_ref_is_not_a_candidate(self):
        mod = _import_fix_refs()
        ref = {"is_src_id": True, "ref_path": "SRC-0001"}
        assert mod.classify_migration(ref, self._units()) == "src_id"

    def test_exact_match_is_migratable(self):
        mod = _import_fix_refs()
        ref = {
            "is_src_id": False, "ref_path": "src/app.py",
            "ref_start": 10, "ref_end": 42,
        }
        assert mod.classify_migration(ref, self._units()) == "exact"

    def test_file_not_in_source_map(self):
        mod = _import_fix_refs()
        ref = {
            "is_src_id": False, "ref_path": "README.md",
            "ref_start": 1, "ref_end": 5,
        }
        assert mod.classify_migration(ref, self._units()) == "no_source_map"

    def test_range_outside_any_unit(self):
        mod = _import_fix_refs()
        ref = {
            "is_src_id": False, "ref_path": "src/app.py",
            "ref_start": 100, "ref_end": 150,
        }
        assert mod.classify_migration(ref, self._units()) == "range_mismatch"

    def test_partial_overlap(self):
        """Start line inside a unit but range differs → not migratable."""
        mod = _import_fix_refs()
        ref = {
            "is_src_id": False, "ref_path": "src/app.py",
            "ref_start": 10, "ref_end": 50,  # extends beyond unit end 42
        }
        assert mod.classify_migration(ref, self._units()) == "partial"


class TestFindUnitForRef:
    """find_unit_for_ref() returns the exact-matching unit or None."""

    def test_returns_matching_unit(self):
        mod = _import_fix_refs()
        units = {
            "src/app.py": [
                {"id": "SRC-0001", "path": "src/app.py", "line_range": [10, 42]},
            ]
        }
        ref = {
            "is_src_id": False, "ref_path": "src/app.py",
            "ref_start": 10, "ref_end": 42,
        }
        unit = mod.find_unit_for_ref(ref, units)
        assert unit is not None
        assert unit["id"] == "SRC-0001"

    def test_no_match_returns_none(self):
        mod = _import_fix_refs()
        units = {
            "src/app.py": [
                {"id": "SRC-0001", "path": "src/app.py", "line_range": [10, 42]},
            ]
        }
        ref = {
            "is_src_id": False, "ref_path": "src/app.py",
            "ref_start": 100, "ref_end": 150,
        }
        assert mod.find_unit_for_ref(ref, units) is None


class TestMigrateSrcidCli:
    """End-to-end --migrate-srcid CLI behaviour."""

    def _setup(self, tmp_path):
        specback = tmp_path / ".specback"
        specback.mkdir()
        (specback / "source-map.json").write_text(json.dumps({
            "units": [
                {"id": "SRC-0001", "path": "src/app.py", "line_range": [10, 42]},
                {"id": "SRC-0002", "path": "src/app.py", "line_range": [60, 80]},
            ]
        }), encoding="utf-8")
        drafts = specback / "drafts"
        drafts.mkdir()
        (drafts / "01-overview.md").write_text(
            "# Overview\n\n"
            "<!-- REF: src/app.py:10-42 -->\n"      # exact → migratable
            "<!-- REF: src/app.py:60-80 -->\n"      # exact → migratable
            "<!-- REF: README.md:1-5 -->\n"          # not in source-map
            "<!-- REF: src/app.py:10-50 -->\n"      # partial overlap
            "<!-- REF: SRC-0001 -->\n",              # already SRC-ID
            encoding="utf-8",
        )
        return specback, drafts

    def test_help_includes_migrate_flag(self):
        result = _run("--help")
        assert result.returncode == 0
        assert "--migrate-srcid" in result.stdout

    def test_dry_run_reports_but_does_not_modify(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
        )
        assert result.returncode == 0
        assert "Migratable (exact unit match)" in result.stdout
        assert "SRC-0001" in result.stdout
        # Dry-run must not modify the file
        content = (drafts / "01-overview.md").read_text(encoding="utf-8")
        assert "<!-- REF: src/app.py:10-42 -->" in content

    def test_apply_converts_exact_matches_only(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        assert result.returncode == 0
        content = (drafts / "01-overview.md").read_text(encoding="utf-8")
        # Exact matches converted
        assert "<!-- REF: SRC-0001 -->" in content
        assert "<!-- REF: SRC-0002 -->" in content
        # Non-migratable refs untouched
        assert "<!-- REF: README.md:1-5 -->" in content
        assert "<!-- REF: src/app.py:10-50 -->" in content

    def test_backup_created_on_apply(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        backup = specback / "backups" / "01-overview.md.bak"
        assert backup.exists()
        assert "<!-- REF: src/app.py:10-42 -->" in backup.read_text(encoding="utf-8")

    def test_missing_source_map_errors(self, tmp_path):
        specback = tmp_path / ".specback"
        specback.mkdir()
        drafts = specback / "drafts"
        drafts.mkdir()
        (drafts / "01-overview.md").write_text("hi", encoding="utf-8")
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
        )
        assert result.returncode == 2

    def test_json_output_shape(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--json",
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["summary"]["migratable"] == 2
        assert report["summary"]["not_migratable"] == 2
        assert report["summary"]["refs_already_src_id"] == 1
