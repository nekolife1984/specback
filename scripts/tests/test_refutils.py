"""Tests for scripts/refutils.py — shared REF parsing/resolver helpers (Issue #281).

Covers:
- REF_RE / SRC_REF_RE marker syntax (single source of truth)
- find_refs_in_text() metadata extraction (path:line and SRC-ID forms)
- index_units_by_path() path → sorted-units index
- units_for_path() exact + suffix path resolution
- line_ranges_overlap() REF↔unit range overlap test
- count_refs() per-line marker counting
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add the scripts directory to sys.path so we can import refutils directly.
SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refutils


# ═══════════════════════════════════════════════════════════════════════════
# REF_RE / SRC_REF_RE
# ═══════════════════════════════════════════════════════════════════════════


class TestRegexes:
    def test_ref_re_matches_path_line(self):
        matches = refutils.REF_RE.findall("<!-- REF: src/a.py:1-5 -->")
        assert matches == [("src/a.py", "1", "5")]

    def test_ref_re_matches_single_line(self):
        # NB: re.findall renders a non-participating optional group as ''
        # (not None); the callers use m.group(3), which is None, instead.
        matches = refutils.REF_RE.findall("<!-- REF: src/a.py:42 -->")
        assert matches == [("src/a.py", "42", "")]

    def test_src_ref_re_matches_src_id(self):
        matches = refutils.SRC_REF_RE.findall("<!-- REF: SRC-0142 -->")
        assert matches == ["SRC-0142"]

    def test_src_ref_re_does_not_match_path_line(self):
        matches = refutils.SRC_REF_RE.findall("<!-- REF: src/a.py:1-5 -->")
        assert matches == []

    def test_ref_re_does_not_match_src_id(self):
        matches = refutils.REF_RE.findall("<!-- REF: SRC-0142 -->")
        assert matches == []

    def test_no_false_overlap_between_forms(self):
        line = "<!-- REF: SRC-0001 --> and <!-- REF: src/a.py:1-5 -->"
        assert refutils.SRC_REF_RE.findall(line) == ["SRC-0001"]
        assert refutils.REF_RE.findall(line) == [("src/a.py", "1", "5")]


# ═══════════════════════════════════════════════════════════════════════════
# find_refs_in_text
# ═══════════════════════════════════════════════════════════════════════════


class TestFindRefsInText:
    def test_path_line_single(self):
        refs = refutils.find_refs_in_text("text <!-- REF: src/a.py:42 --> tail\n")
        assert len(refs) == 1
        r = refs[0]
        assert r["line_no"] == 0
        assert r["ref_path"] == "src/a.py"
        assert r["ref_start"] == 42
        assert r["ref_end"] == 42
        assert r["is_src_id"] is False
        assert r["full_match"] == "<!-- REF: src/a.py:42 -->"
        assert r["col_start"] == 5
        assert r["col_end"] == 5 + len("<!-- REF: src/a.py:42 -->")

    def test_path_line_range(self):
        refs = refutils.find_refs_in_text("<!-- REF: src/a.py:10-42 -->\n")
        r = refs[0]
        assert r["ref_start"] == 10
        assert r["ref_end"] == 42

    def test_src_id_form(self):
        refs = refutils.find_refs_in_text("<!-- REF: SRC-0142 -->\n")
        assert len(refs) == 1
        r = refs[0]
        assert r["ref_path"] == "SRC-0142"
        assert r["ref_start"] == 0
        assert r["ref_end"] == 0
        assert r["is_src_id"] is True

    def test_src_id_precedes_path_on_same_line(self):
        refs = refutils.find_refs_in_text(
            "refs: <!-- REF: SRC-0001 --> and <!-- REF: app/errors.py:1-50 -->\n"
        )
        assert [r["ref_path"] for r in refs] == ["SRC-0001", "app/errors.py"]
        assert refs[0]["is_src_id"] is True
        assert refs[1]["is_src_id"] is False

    def test_multiple_path_refs_on_same_line_in_column_order(self):
        refs = refutils.find_refs_in_text("<!-- REF: a.py:1 --> <!-- REF: b.py:2 -->\n")
        assert [r["ref_path"] for r in refs] == ["a.py", "b.py"]
        assert refs[0]["col_start"] < refs[1]["col_start"]

    def test_line_numbers_across_lines(self):
        refs = refutils.find_refs_in_text(
            "no ref\n<!-- REF: a.py:1 -->\n<!-- REF: b.py:2-3 -->\n"
        )
        assert [r["line_no"] for r in refs] == [1, 2]

    def test_no_refs_returns_empty(self):
        assert refutils.find_refs_in_text("plain text, no markers\n") == []

    def test_ignores_bracket_form(self):
        # [REF: path:line] is deprecated and must NOT be parsed.
        assert refutils.find_refs_in_text("[REF: src/a.py:1-5]") == []


# ═══════════════════════════════════════════════════════════════════════════
# index_units_by_path
# ═══════════════════════════════════════════════════════════════════════════


class TestIndexUnitsByPath:
    def test_groups_units_by_path(self):
        units = [
            {"id": "SRC-0001", "path": "src/a.py", "line_range": [1, 10]},
            {"id": "SRC-0002", "path": "src/b.py", "line_range": [5, 20]},
        ]
        by_path = refutils.index_units_by_path(units)
        assert set(by_path.keys()) == {"src/a.py", "src/b.py"}
        assert by_path["src/a.py"][0]["id"] == "SRC-0001"

    def test_sorts_units_by_line_range_start(self):
        units = [
            {"id": "SRC-0002", "path": "a.py", "line_range": [20, 30]},
            {"id": "SRC-0001", "path": "a.py", "line_range": [1, 10]},
        ]
        by_path = refutils.index_units_by_path(units)
        assert [u["id"] for u in by_path["a.py"]] == ["SRC-0001", "SRC-0002"]

    def test_skips_units_without_path(self):
        units = [
            {"id": "SRC-0001", "line_range": [1, 10]},  # no path
            {"id": "SRC-0002", "path": "a.py", "line_range": [1, 10]},
        ]
        by_path = refutils.index_units_by_path(units)
        assert "a.py" in by_path
        assert len(by_path["a.py"]) == 1

    def test_empty_input(self):
        assert refutils.index_units_by_path([]) == {}


# ═══════════════════════════════════════════════════════════════════════════
# units_for_path
# ═══════════════════════════════════════════════════════════════════════════


class TestUnitsForPath:
    def _index(self):
        return refutils.index_units_by_path([
            {"id": "SRC-1", "path": "app/a.py", "line_range": [1, 10]},
            {"id": "SRC-2", "path": "lib/helpers/util.py", "line_range": [1, 5]},
        ])

    def test_exact_match(self):
        found = refutils.units_for_path("app/a.py", self._index())
        assert [u["id"] for u in found] == ["SRC-1"]

    def test_suffix_match_on_ref_path(self):
        found = refutils.units_for_path("util.py", self._index())
        assert [u["id"] for u in found] == ["SRC-2"]

    def test_suffix_match_on_unit_path(self):
        found = refutils.units_for_path("lib/helpers/util.py", self._index())
        assert [u["id"] for u in found] == ["SRC-2"]

    def test_no_match_returns_empty(self):
        assert refutils.units_for_path("nope.py", self._index()) == []


# ═══════════════════════════════════════════════════════════════════════════
# line_ranges_overlap
# ═══════════════════════════════════════════════════════════════════════════


class TestLineRangesOverlap:
    def test_inside(self):
        assert refutils.line_ranges_overlap(3, 4, 1, 10) is True

    def test_partial_overlap(self):
        assert refutils.line_ranges_overlap(8, 15, 10, 20) is True

    def test_touching_boundary_counts_as_overlap(self):
        # r_end == u_start is NOT r_end < u_start → overlap (legacy behaviour).
        assert refutils.line_ranges_overlap(1, 10, 10, 20) is True

    def test_disjoint_below(self):
        assert refutils.line_ranges_overlap(1, 2, 10, 20) is False

    def test_disjoint_above(self):
        assert refutils.line_ranges_overlap(30, 40, 10, 20) is False


# ═══════════════════════════════════════════════════════════════════════════
# count_refs
# ═══════════════════════════════════════════════════════════════════════════


class TestCountRefs:
    def test_counts_both_forms(self):
        assert refutils.count_refs(
            "<!-- REF: src/a.py:1-5 --> and <!-- REF: SRC-0042 -->"
        ) == 2

    def test_counts_multiple_path_refs(self):
        assert refutils.count_refs(
            "<!-- REF: a.py:1 --> <!-- REF: b.py:2 -->"
        ) == 2

    def test_no_refs(self):
        assert refutils.count_refs("no refs here") == 0
