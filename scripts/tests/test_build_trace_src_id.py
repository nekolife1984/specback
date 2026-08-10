"""Tests for build-trace.py SRC-ID support (Issue #224).

Covers:
- SRC_REF_RE regex pattern matching
- scan_drafts_for_refs() with units_by_id resolution
- Unresolved SRC-ID (not in source-map) fallback
- Mixed SRC-ID + path:line refs on same line
- units_by_id=None graceful fallback
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# We import the script as a module for unit-testing internals
SCRIPT = Path(__file__).resolve().parent.parent / "build-trace.py"


def _import_build_trace():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_trace_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_source_map(units: list[dict]) -> Path:
    """Create a temporary source-map.json and return its path."""
    import tempfile
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps({"units": units}), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════
# SRC_REF_RE: regex unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSrcRefRe:
    def test_matches_src_id(self):
        mod = _import_build_trace()
        matches = mod.SRC_REF_RE.findall("<!-- REF: SRC-0142 -->")
        assert matches == ["SRC-0142"]

    def test_matches_src_id_with_spaces(self):
        mod = _import_build_trace()
        matches = mod.SRC_REF_RE.findall("<!-- REF:   SRC-0001   -->")
        assert matches == ["SRC-0001"]

    def test_does_not_match_path_line(self):
        mod = _import_build_trace()
        matches = mod.SRC_REF_RE.findall("<!-- REF: src/errors.py:1-50 -->")
        assert matches == []

    def test_does_not_match_invalid_id(self):
        mod = _import_build_trace()
        matches = mod.SRC_REF_RE.findall("<!-- REF: SRC-XXX -->")
        assert matches == []

    def test_matches_multiple_on_same_line(self):
        mod = _import_build_trace()
        matches = mod.SRC_REF_RE.findall(
            "<!-- REF: SRC-0001 --> and <!-- REF: SRC-0142 -->"
        )
        assert matches == ["SRC-0001", "SRC-0142"]


# ═══════════════════════════════════════════════════════════════════════════
# scan_drafts_for_refs: SRC-ID resolution
# ═══════════════════════════════════════════════════════════════════════════


class TestScanDraftsForRefsSrcId:
    def test_resolves_known_src_id(self, tmp_path):
        """SRC-ID that exists in units_by_id should resolve to path+line."""
        mod = _import_build_trace()
        units_by_id = {
            "SRC-0142": {
                "id": "SRC-0142",
                "path": "app/models/issue.rb",
                "line_range": [10, 42],
                "kind": "class",
                "name": "Issue",
            }
        }
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()
        (drafts_dir / "01-overview.md").write_text(
            "# Chapter\n\n<!-- REF: SRC-0142 -->\n", encoding="utf-8"
        )
        refs = mod.scan_drafts_for_refs(drafts_dir, units_by_id)
        assert len(refs) == 1
        assert refs[0]["ref_path"] == "app/models/issue.rb"
        assert refs[0]["ref_start"] == 10
        assert refs[0]["ref_end"] == 42
        assert refs[0]["draft_file"] == "01-overview.md"

    def test_unresolved_src_id_gets_zero_range(self, tmp_path):
        """SRC-ID not in units_by_id should record with ref_start=ref_end=0."""
        mod = _import_build_trace()
        units_by_id: dict = {}  # empty — no source-map available
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()
        (drafts_dir / "01-overview.md").write_text(
            "<!-- REF: SRC-9999 -->\n", encoding="utf-8"
        )
        refs = mod.scan_drafts_for_refs(drafts_dir, units_by_id)
        assert len(refs) == 1
        assert refs[0]["ref_path"] == "SRC-9999"
        assert refs[0]["ref_start"] == 0
        assert refs[0]["ref_end"] == 0

    def test_null_units_by_id_fallback(self, tmp_path):
        """units_by_id=None should treat SRC-ID refs as unresolved."""
        mod = _import_build_trace()
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()
        (drafts_dir / "01-overview.md").write_text(
            "<!-- REF: SRC-0142 -->\n", encoding="utf-8"
        )
        refs = mod.scan_drafts_for_refs(drafts_dir, None)
        assert len(refs) == 1
        assert refs[0]["ref_path"] == "SRC-0142"
        assert refs[0]["ref_start"] == 0
        assert refs[0]["ref_end"] == 0

    def test_mixed_src_id_and_path_line_on_same_line(self, tmp_path):
        """Both SRC-ID and path:line refs on the same line should be captured."""
        mod = _import_build_trace()
        units_by_id = {
            "SRC-0142": {
                "id": "SRC-0142",
                "path": "app/models/issue.rb",
                "line_range": [10, 42],
                "kind": "class",
                "name": "Issue",
            }
        }
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()
        (drafts_dir / "01-overview.md").write_text(
            "refs: <!-- REF: SRC-0142 --> and <!-- REF: app/errors.py:1-50 -->\n",
            encoding="utf-8",
        )
        refs = mod.scan_drafts_for_refs(drafts_dir, units_by_id)
        assert len(refs) == 2, f"Expected 2 refs, got {len(refs)}"
        # SRC-ID should be first (scanned before path:line)
        assert refs[0]["ref_path"] == "app/models/issue.rb"
        assert refs[1]["ref_path"] == "app/errors.py"

    def test_multiple_src_ids_on_same_line(self, tmp_path):
        """Multiple SRC-ID refs on the same line should all be captured."""
        mod = _import_build_trace()
        units_by_id = {
            "SRC-0001": {"id": "SRC-0001", "path": "src/a.py", "line_range": [1, 10], "kind": "class", "name": "A"},
            "SRC-0002": {"id": "SRC-0002", "path": "src/b.py", "line_range": [5, 20], "kind": "class", "name": "B"},
        }
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()
        (drafts_dir / "01-overview.md").write_text(
            "refs: <!-- REF: SRC-0001 --> and <!-- REF: SRC-0002 -->\n",
            encoding="utf-8",
        )
        refs = mod.scan_drafts_for_refs(drafts_dir, units_by_id)
        assert len(refs) == 2, f"Expected 2 refs, got {len(refs)}"
        paths = [r["ref_path"] for r in refs]
        assert "src/a.py" in paths
        assert "src/b.py" in paths

    def test_skips_empty_drafts_dir(self, tmp_path):
        """Empty drafts directory should return empty list."""
        mod = _import_build_trace()
        drafts_dir = tmp_path / "empty_drafts"
        drafts_dir.mkdir()
        refs = mod.scan_drafts_for_refs(drafts_dir, {})
        assert refs == []


# ═══════════════════════════════════════════════════════════════════════════
# Core helpers (Issue #258 — load_exclusions / is_excluded / parse_section_at
#                            / resolve_refs_to_units / load_source_map)
# ═══════════════════════════════════════════════════════════════════════════


class TestLoaders:
    def test_load_source_map(self, tmp_path):
        mod = _import_build_trace()
        p = tmp_path / "source-map.json"
        p.write_text(json.dumps({"units": [{"id": "SRC-1"}]}), encoding="utf-8")
        assert mod.load_source_map(p) == {"units": [{"id": "SRC-1"}]}

    def test_load_source_map_missing_raises(self, tmp_path):
        mod = _import_build_trace()
        import pytest
        with pytest.raises(FileNotFoundError):
            mod.load_source_map(tmp_path / "nope.json")

    def test_load_exclusions_missing_returns_empty(self, tmp_path):
        mod = _import_build_trace()
        assert mod.load_exclusions(tmp_path / "nope.yml") == []

    def test_load_exclusions_minimal_yaml(self, tmp_path):
        mod = _import_build_trace()
        p = tmp_path / "exclusions.yml"
        p.write_text(
            "exclusions:\n"
            "  - source_id: SRC-0001\n"
            "    reason: legacy\n"
            "  - path_glob: '**/generated/**'\n"
            "    reason: autogenerated\n",
            encoding="utf-8",
        )
        items = mod.load_exclusions(p)
        assert any(e.get("source_id") == "SRC-0001" for e in items)
        assert any("path_glob" in e for e in items)


class TestIsExcluded:
    def test_by_source_id(self):
        mod = _import_build_trace()
        unit = {"id": "SRC-0001", "path": "a.py"}
        ok, reason = mod.is_excluded(unit, [{"source_id": "SRC-0001", "reason": "legacy"}])
        assert ok is True
        assert reason == "legacy"

    def test_by_path_glob(self):
        mod = _import_build_trace()
        unit = {"id": "SRC-2", "path": "app/generated/x.py"}
        ok, _ = mod.is_excluded(unit, [{"path_glob": "**/generated/**"}])
        assert ok is True

    def test_by_path_glob_direct_child(self):
        """fnmatch ** does not cross '/', so a top-level path must match directly."""
        mod = _import_build_trace()
        unit = {"id": "SRC-2b", "path": "generated/x.py"}
        ok, _ = mod.is_excluded(unit, [{"path_glob": "generated/*"}])
        assert ok is True

    def test_not_excluded(self):
        mod = _import_build_trace()
        unit = {"id": "SRC-3", "path": "app/main.py"}
        ok, reason = mod.is_excluded(unit, [{"source_id": "SRC-0001"}])
        assert ok is False
        assert reason is None


class TestParseSectionAt:
    def test_nearest_heading(self):
        mod = _import_build_trace()
        lines = ["# Top", "text", "## Section", "body", "more"]
        assert mod.parse_section_at(lines, 3) == "Section"

    def test_prelude_when_no_heading(self):
        mod = _import_build_trace()
        lines = ["plain text", "more"]
        assert mod.parse_section_at(lines, 1) == "(prelude)"


class TestResolveRefsToUnits:
    def test_exact_and_overlap(self):
        mod = _import_build_trace()
        units = [
            {"id": "SRC-1", "path": "app/a.py", "line_range": [1, 10]},
            {"id": "SRC-2", "path": "app/b.py", "line_range": [5, 8]},
        ]
        refs = [
            {"draft_file": "01.md", "section": "S", "ref_path": "app/a.py",
             "ref_start": 3, "ref_end": 4},
        ]
        coverage = mod.resolve_refs_to_units(refs, units)
        assert coverage["SRC-1"] == [{"file": "01.md", "section": "S"}]
        assert coverage["SRC-2"] == []

    def test_suffix_match(self):
        mod = _import_build_trace()
        units = [{"id": "SRC-1", "path": "lib/helpers/util.py", "line_range": [1, 5]}]
        refs = [
            {"draft_file": "01.md", "section": "S", "ref_path": "util.py",
             "ref_start": 1, "ref_end": 2},
        ]
        coverage = mod.resolve_refs_to_units(refs, units)
        assert len(coverage["SRC-1"]) == 1

    def test_no_overlap(self):
        mod = _import_build_trace()
        units = [{"id": "SRC-1", "path": "app/a.py", "line_range": [10, 20]}]
        refs = [
            {"draft_file": "01.md", "section": "S", "ref_path": "app/a.py",
             "ref_start": 1, "ref_end": 2},
        ]
        coverage = mod.resolve_refs_to_units(refs, units)
        assert coverage["SRC-1"] == []
