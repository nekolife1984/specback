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

import pytest

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
        # Backup is timestamped (Issue #248 / H-8: no fixed-name overwrite)
        backups = sorted((specback / "backups").glob("01-overview.md.*.bak"))
        assert len(backups) == 1, [p.name for p in backups]
        backup = backups[0]
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

    # -- Issue #248 / FIX-1: position-based replacement --

    def test_apply_targets_recorded_position(self, tmp_path):
        """Duplicate marker text: only the recorded position is rewritten (both)."""
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text(
            "# Overview\n\n"
            "<!-- REF: src/app.py:10-42 -->\n"
            "\n"
            "```\n"
            "<!-- REF: src/app.py:10-42 -->\n"
            "```\n",
            encoding="utf-8",
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        assert result.returncode == 0, result.stderr
        content = (drafts / "01-overview.md").read_text(encoding="utf-8")
        # Both occurrences (real marker + code example) were converted at their
        # own positions; with str.replace(...,1) the second one would be left
        # as a path:line ref and the first would be double-converted.
        assert content.count("<!-- REF: SRC-0001 -->") == 2
        assert "<!-- REF: src/app.py:10-42 -->" not in content

    def test_apply_handles_no_space_variant(self, tmp_path):
        """<!-- REF:a.py:10 --> without space after REF: is replaced at its position."""
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text(
            "# Overview\n\n<!-- REF:src/app.py:10-42 -->\n",
            encoding="utf-8",
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        assert result.returncode == 0, result.stderr
        content = (drafts / "01-overview.md").read_text(encoding="utf-8")
        assert "<!-- REF: SRC-0001 -->" in content
        assert "<!-- REF:src/app.py:10-42 -->" not in content

    # -- Issue #248 / H-1: git ref validation --

    def test_rejects_option_like_state_commit(self, tmp_path):
        """A malicious generated_at_commit in state.json is refused (no git arg injection)."""
        specback, drafts = self._setup(tmp_path)
        (specback / "state.json").write_text(json.dumps({
            "generated_at_commit": "--output=/tmp/pwned",
        }), encoding="utf-8")
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
        )
        assert result.returncode == 1
        assert "invalid git ref" in result.stderr

    # -- Issue #248 / Y8: flag combination validation --

    def test_migrate_conflicts_with_diff(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--diff", "raw-diff-text",
        )
        assert result.returncode == 2
        assert "cannot be combined" in result.stderr

    def test_migrate_with_check_warns(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--check",
        )
        assert result.returncode == 0
        assert "--check has no effect" in result.stderr

    # -- Issue #248 / H-2: symlink refusal --

    def test_apply_refuses_symlink(self, tmp_path):
        """A symlinked spec file is skipped; the link target stays untouched."""
        specback, drafts = self._setup(tmp_path)
        target = tmp_path / "outside.md"
        target.write_text("<!-- REF: src/app.py:10-42 -->\n", encoding="utf-8")
        (drafts / "01-overview.md").unlink()
        (drafts / "01-overview.md").symlink_to(target)
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        assert result.returncode == 0
        assert "refusing to process symlink" in result.stderr
        assert "<!-- REF: src/app.py:10-42 -->" in target.read_text(encoding="utf-8")

    # -- Issue #248 / H-11: duplicate unit detection --

    def test_duplicate_units_refuse_migration(self, tmp_path):
        """Ambiguous duplicate (path, line_range) units abort the migration."""
        specback = tmp_path / ".specback"
        specback.mkdir()
        (specback / "source-map.json").write_text(json.dumps({
            "units": [
                {"id": "SRC-0001", "path": "src/app.py", "line_range": [10, 42]},
                {"id": "SRC-0002", "path": "src/app.py", "line_range": [10, 42]},
            ]
        }), encoding="utf-8")
        drafts = specback / "drafts"
        drafts.mkdir()
        (drafts / "01-overview.md").write_text(
            "<!-- REF: src/app.py:10-42 -->\n", encoding="utf-8"
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--migrate-srcid",
            "--apply",
        )
        assert result.returncode == 1
        assert "duplicate units" in result.stderr


class TestReplaceAt:
    """Unit tests for the position-based replacement helper (Issue #248 / FIX-1)."""

    def test_replaces_exact_position_only(self):
        mod = _import_fix_refs()
        marker = "<!-- REF: a.py:1-5 -->"
        content = "# H\n\n" + marker + "\n" + marker + "\n"
        out = mod.replace_at(content, 2, 0, len(marker), "<!-- REF: SRC-0001 -->", expected=marker)
        lines = out.splitlines()
        assert lines[2] == "<!-- REF: SRC-0001 -->"
        assert lines[3] == marker  # second occurrence untouched

    def test_mismatch_raises(self):
        mod = _import_fix_refs()
        marker = "<!-- REF: a.py:1-5 -->"
        content = marker + "\n"
        with pytest.raises(RuntimeError):
            mod.replace_at(content, 0, 0, len(marker), "X", expected="<!-- REF: b.py:9 -->")


class TestParseHunks:
    """Hunk parsing (Issue #249 / F1): body lines are captured for exact mapping."""

    def test_standard_diff_with_body(self):
        mod = _import_fix_refs()
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -10,3 +5,2 @@\n"
            "-old\n"
            "-old2\n"
            "+new\n"
            "+new2\n"
            "\n"
            " def x():\n"
        )
        files = mod.parse_hunks(diff)
        assert "src/app.py" in files
        hunk = files["src/app.py"][0]
        assert hunk["old_start"] == 10
        assert hunk["old_count"] == 3
        assert hunk["new_start"] == 5
        assert hunk["new_count"] == 2
        assert hunk["lines"][0] == ("-", "old")
        assert hunk["lines"][2] == ("+", "new")
        assert hunk["lines"][4] == (" ", "def x():")

    def test_new_file_dev_null(self):
        mod = _import_fix_refs()
        diff = (
            "diff --git a/src/new.py b/src/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/src/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+import os\n"
            "+def f():\n"
            "    pass\n"
        )
        files = mod.parse_hunks(diff)
        assert "src/new.py" in files
        assert files["src/new.py"][0]["old_start"] == 0
        assert files["src/new.py"][0]["new_start"] == 1

    def test_filename_with_space(self):
        mod = _import_fix_refs()
        diff = (
            "diff --git a/src/my file.py b/src/my file.py\n"
            "--- a/src/my file.py\n"
            "+++ b/src/my file.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        files = mod.parse_hunks(diff)
        assert "src/my file.py" in files

    def test_count_omitted_means_one(self):
        mod = _import_fix_refs()
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -5 +6 @@\n"
            "-x\n"
            "+y\n"
        )
        files = mod.parse_hunks(diff)
        hunk = files["a.py"][0]
        assert hunk["old_count"] == 1
        assert hunk["new_count"] == 1

    def test_no_hunks_returns_empty(self):
        mod = _import_fix_refs()
        assert mod.parse_hunks("") == {}


class TestBuildLineMap:
    """Line mapping (Issue #249 / F2): body-based exact mapping + fallback."""

    def test_deleted_lines_anywhere_map_to_none(self):
        """Deletions at the hunk START are now exact, not approximated at tail."""
        mod = _import_fix_refs()
        hunks = [{
            "old_start": 10, "old_count": 3, "new_start": 5, "new_count": 2,
            "lines": [("-", "a"), ("-", "b"), ("-", "c"), ("+", "d"), ("+", "e")],
        }]
        assert mod.build_line_map(hunks) == {10: None, 11: None, 12: None}

    def test_mixed_delete_context_insert(self):
        """Deletion at hunk start: context lines map from new_start onward."""
        mod = _import_fix_refs()
        hunks = [{
            "old_start": 10, "old_count": 3, "new_start": 5, "new_count": 3,
            "lines": [("-", "a"), (" ", "b"), (" ", "c"), ("+", "d")],
        }]
        # old 10 deleted; context "b"→new 5, "c"→new 6; insertion "d"→new 7
        assert mod.build_line_map(hunks) == {10: None, 11: 5, 12: 6}

    def test_pure_shift(self):
        mod = _import_fix_refs()
        hunks = [{
            "old_start": 10, "old_count": 2, "new_start": 11, "new_count": 2,
            "lines": [(" ", "a"), (" ", "b")],
        }]
        assert mod.build_line_map(hunks) == {10: 11, 11: 12}

    def test_insertion_only_no_old_lines(self):
        mod = _import_fix_refs()
        hunks = [{
            "old_start": 10, "old_count": 0, "new_start": 5, "new_count": 2,
            "lines": [("+", "a"), ("+", "b")],
        }]
        assert mod.build_line_map(hunks) == {}

    def test_header_only_fallback_deletions_at_tail(self):
        """Header-only hunks keep the documented approximation (spec-fixed)."""
        mod = _import_fix_refs()
        hunks = [{
            "old_start": 10, "old_count": 3, "new_start": 5, "new_count": 2,
            "lines": [],
        }]
        assert mod.build_line_map(hunks) == {10: 5, 11: 6, 12: None}

    def test_multiple_hunks(self):
        mod = _import_fix_refs()
        hunks = [
            {"old_start": 1, "old_count": 1, "new_start": 1, "new_count": 1,
             "lines": [(" ", "a")]},
            {"old_start": 10, "old_count": 1, "new_start": 11, "new_count": 1,
             "lines": [("-", "x")]},
        ]
        assert mod.build_line_map(hunks) == {1: 1, 10: None}

    def test_empty_hunks(self):
        mod = _import_fix_refs()
        assert mod.build_line_map([]) == {}


class TestApplyLineShift:
    """apply_line_shift semantics (Issue #249 / F1)."""

    def test_start_shifted_only(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, None, {10: 11}) == (11, None)

    def test_start_shifted_end_outside_map_applies_delta(self):
        """End outside any hunk inherits the start's delta (Issue #249 / F3)."""
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, 42, {10: 11}) == (11, 43)

    def test_end_shifted_only(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, 42, {42: 43}) == (10, 43)

    def test_start_deleted_is_orphan(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, 42, {10: None}) == (None, None)

    def test_end_deleted_is_orphan(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, 42, {42: None}) == (None, None)

    def test_outside_hunk_unchanged(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(20, 30, {10: 11}) == (20, 30)

    def test_single_line_unchanged(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, None, {10: 10}) == (10, None)

    def test_both_shifted(self):
        mod = _import_fix_refs()
        assert mod.apply_line_shift(10, 42, {10: 12, 42: 44}) == (12, 44)


class TestFormatRefSrcId:
    """format_ref SRC-ID form detection (Issue #249 / F6 regression)."""

    def test_src_id_forced(self):
        mod = _import_fix_refs()
        ref = {"ref_path": "SRC-0001", "ref_start": 0, "ref_end": 0}
        assert mod.format_ref(ref, is_src_id=True) == "<!-- REF: SRC-0001 -->"

    def test_path_starting_with_src_is_not_src_id(self):
        """SRC-notes.md:10 must stay a path:line ref (startswith('SRC-') is gone)."""
        mod = _import_fix_refs()
        ref = {"ref_path": "SRC-notes.md", "ref_start": 10, "ref_end": 10}
        assert mod.format_ref(ref) == "<!-- REF: SRC-notes.md:10 -->"

    def test_path_range(self):
        mod = _import_fix_refs()
        ref = {"ref_path": "src/app.py", "ref_start": 10, "ref_end": 42}
        assert mod.format_ref(ref) == "<!-- REF: src/app.py:10-42 -->"

    def test_scanned_src_id_ref_inferred(self):
        mod = _import_fix_refs()
        ref = {"ref_path": "SRC-0001", "ref_start": 0, "ref_end": 0, "is_src_id": True}
        assert mod.format_ref(ref) == "<!-- REF: SRC-0001 -->"


class TestFindRefsInFileDetailed:
    """find_refs_in_file extraction details (Issue #249 / F9)."""

    @staticmethod
    def _write(tmp_path: Path, content: str) -> Path:
        p = tmp_path / "spec.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_extracts_positions(self, tmp_path):
        mod = _import_fix_refs()
        marker = "<!-- REF: src/app.py:10-42 -->"
        p = self._write(tmp_path, "text " + marker + " tail\n")
        refs = mod.find_refs_in_file(p)
        assert len(refs) == 1
        r = refs[0]
        assert r["line_no"] == 0
        assert r["ref_start"] == 10
        assert r["ref_end"] == 42
        assert r["col_start"] == 5
        assert r["col_end"] == 5 + len(marker)
        assert r["full_match"] == marker
        assert r["is_src_id"] is False

    def test_two_refs_on_one_line(self, tmp_path):
        mod = _import_fix_refs()
        p = self._write(tmp_path, "<!-- REF: a.py:1 --> <!-- REF: b.py:2 -->\n")
        refs = mod.find_refs_in_file(p)
        assert len(refs) == 2
        assert [r["ref_path"] for r in refs] == ["a.py", "b.py"]
        assert refs[0]["col_start"] < refs[1]["col_start"]

    def test_mixed_src_id_and_path_refs(self, tmp_path):
        mod = _import_fix_refs()
        p = self._write(tmp_path, "<!-- REF: SRC-0001 -->\n<!-- REF: a.py:1-5 -->\n")
        refs = mod.find_refs_in_file(p)
        assert len(refs) == 2
        assert refs[0]["is_src_id"] is True
        assert refs[0]["ref_path"] == "SRC-0001"
        assert refs[1]["is_src_id"] is False
        assert refs[1]["ref_path"] == "a.py"


class TestLoadSourceMapSort:
    """load_source_map sorting and path filtering (Issue #249 / F7)."""

    def test_sorts_units_by_line_range(self, tmp_path):
        mod = _import_fix_refs()
        specback = tmp_path / ".specback"
        specback.mkdir()
        (specback / "source-map.json").write_text(json.dumps({
            "units": [
                {"id": "SRC-0002", "path": "a.py", "line_range": [20, 30]},
                {"id": "SRC-0001", "path": "a.py", "line_range": [1, 10]},
            ]
        }), encoding="utf-8")
        by_path = mod.load_source_map(specback)
        assert [u["id"] for u in by_path["a.py"]] == ["SRC-0001", "SRC-0002"]

    def test_skips_units_without_path(self, tmp_path):
        mod = _import_fix_refs()
        specback = tmp_path / ".specback"
        specback.mkdir()
        (specback / "source-map.json").write_text(json.dumps({
            "units": [
                {"id": "SRC-0001", "line_range": [1, 10]},  # no path
                {"id": "SRC-0002", "path": "a.py", "line_range": [1, 10]},
            ]
        }), encoding="utf-8")
        by_path = mod.load_source_map(specback)
        assert "a.py" in by_path
        assert len(by_path["a.py"]) == 1


class TestMainlineCli:
    """Phase 7b mainline E2E: --diff → corrections → --apply → --check (Issue #249 / F3, F4)."""

    DIFF_SHIFT = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -10,2 +11,2 @@\n"
        " def a():\n"
        "     pass\n"
    )

    DIFF_DELETE = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -10,3 +5,2 @@\n"
        "-old10\n"
        "-old11\n"
        "-old12\n"
        "+new5\n"
        "+new6\n"
    )

    @staticmethod
    def _setup(tmp_path: Path):
        specback = tmp_path / ".specback"
        specback.mkdir()
        drafts = specback / "drafts"
        drafts.mkdir()
        return specback, drafts

    def test_mainline_dry_run_then_apply(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text(
            "# Overview\n\n<!-- REF: src/app.py:10-42 -->\n", encoding="utf-8"
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--diff", self.DIFF_SHIFT,
        )
        assert result.returncode == 0
        assert "Line corrections needed" in result.stdout
        # dry-run leaves the file untouched
        assert "<!-- REF: src/app.py:10-42 -->" in (
            drafts / "01-overview.md").read_text(encoding="utf-8")

        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--diff", self.DIFF_SHIFT,
            "--apply",
        )
        assert result.returncode == 0, result.stderr
        content = (drafts / "01-overview.md").read_text(encoding="utf-8")
        assert "<!-- REF: src/app.py:11-43 -->" in content
        assert "<!-- REF: src/app.py:10-42 -->" not in content

    def test_mainline_check_passes_when_fixed(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text(
            "<!-- REF: src/app.py:10-42 -->\n", encoding="utf-8"
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--diff", self.DIFF_SHIFT,
            "--check",
        )
        assert result.returncode == 0, result.stderr

    def test_mainline_check_fails_on_orphan(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text(
            "<!-- REF: src/app.py:10-42 -->\n", encoding="utf-8"
        )
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--diff", self.DIFF_DELETE,
            "--check",
        )
        assert result.returncode == 1
        assert "CHECK FAILED" in result.stderr

    def test_mainline_empty_diff_exits_zero(self, tmp_path):
        specback, drafts = self._setup(tmp_path)
        (drafts / "01-overview.md").write_text("x", encoding="utf-8")
        result = _run(
            "--specback-dir", str(specback),
            "--output-dir", str(specback),
            "--diff", "",
        )
        assert result.returncode == 0
        assert "No changes detected" in result.stdout
