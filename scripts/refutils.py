#!/usr/bin/env python3
"""
refutils.py — shared REF marker parsing and resolution helpers.

Several scripts previously re-implemented the same regexes and scan/resolve
loops with slightly different shapes: `<!-- REF: path:line -->` parsing,
`<!-- REF: SRC-NNNN -->` parsing, and the path→unit index matching used to
resolve REFs to source units.  This module is the single home for that logic
(Issue #281).

Design notes
------------
* ``REF_RE`` / ``SRC_REF_RE`` are the single source of truth for the marker
  syntax consumed by fix-refs.py, build-trace.py and coverage-check.py.
* ``find_refs_in_text`` returns *metadata* for every marker (line/column
  position, parsed path/range, SRC-ID flag, full match text) so each caller
  can shape the result: fix-refs.py replaces markers in place, build-trace.py
  adds section/draft-file columns, coverage-check.py only counts.
* ``index_units_by_path`` / ``units_for_path`` / ``line_ranges_overlap`` are
  the shared ref→unit resolution primitives used by build-trace.py's resolver
  and by fix-refs.py's SRC-ID migration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Single source of truth for the two supported REF marker forms:
#   <!-- REF: path:start-end -->   direct path + line range reference
#   <!-- REF: SRC-NNNN -->         indirect source-unit ID reference
REF_RE = re.compile(r"<!-- REF:\s*([^:\]]+):(\d+)(?:-(\d+))?\s*-->")
SRC_REF_RE = re.compile(r"<!-- REF:\s*(SRC-\d+)\s*-->")


def find_refs_in_text(text: str) -> list[dict[str, Any]]:
    """Scan *text* for every ``<!-- REF: ... -->`` marker.

    Supports two formats:
    - ``<!-- REF: path:start-end -->`` — direct path + line range reference
    - ``<!-- REF: SRC-NNNN -->`` — indirect source-unit ID reference

    Returns a list of dicts in file order (SRC-ID refs precede path:line refs
    on the same line), each with:
    - line_no: 0-indexed line number in the text
    - col_start / col_end: 0-indexed match span within that line
    - ref_path: path from the marker (or the SRC-ID for indirect refs)
    - ref_start / ref_end: parsed line range (0 for SRC-ID; ``end == start``
      for single-line refs)
    - is_src_id: True when the marker is ``<!-- REF: SRC-NNNN -->``
    - full_match: the exact matched text
    """
    refs: list[dict[str, Any]] = []
    for line_no_0idx, line in enumerate(text.splitlines()):
        # Collect all SRC-ID refs on this line first
        for src_m in SRC_REF_RE.finditer(line):
            src_id = src_m.group(1).strip()
            refs.append({
                "line_no": line_no_0idx,
                "col_start": src_m.start(),
                "col_end": src_m.end(),
                "ref_path": src_id,
                "ref_start": 0,
                "ref_end": 0,
                "is_src_id": True,
                "full_match": src_m.group(0),
            })
        # Also collect any path:line refs on the same line
        for m in REF_RE.finditer(line):
            ref_path = m.group(1).strip()
            ref_start = int(m.group(2))
            ref_end = int(m.group(3)) if m.group(3) else ref_start
            refs.append({
                "line_no": line_no_0idx,
                "col_start": m.start(),
                "col_end": m.end(),
                "ref_path": ref_path,
                "ref_start": ref_start,
                "ref_end": ref_end,
                "is_src_id": False,
                "full_match": m.group(0),
            })
    return refs


def index_units_by_path(units: list[dict]) -> dict[str, list[dict]]:
    """Index source units by their ``path`` field.

    Returns a dict mapping file path → list of units sorted by line_range
    start.  Units without a ``path`` are skipped.
    """
    by_path: dict[str, list[dict]] = {}
    for u in units:
        if isinstance(u, dict) and u.get("path") is not None:
            by_path.setdefault(u["path"], []).append(u)
    for p in by_path:
        by_path[p].sort(key=lambda u: (u.get("line_range") or [0, 0])[0])
    return by_path


def units_for_path(ref_path: str, units_by_path: dict[str, list[dict]]) -> list[dict]:
    """Return candidate units for *ref_path* (exact match, then suffix match).

    Agents sometimes write a path that differs slightly from the source-map
    spelling (e.g. ``util.py`` vs ``lib/helpers/util.py``); a suffix match on
    either side absorbs those small differences.
    """
    candidates: list[dict] = []
    if ref_path in units_by_path:
        candidates.extend(units_by_path[ref_path])
    else:
        for path, ulist in units_by_path.items():
            if path.endswith("/" + ref_path) or ref_path.endswith("/" + path):
                candidates.extend(ulist)
    return candidates


def line_ranges_overlap(r_start: int, r_end: int, u_start: int, u_end: int) -> bool:
    """True when the REF range [r_start, r_end] overlaps the unit range [u_start, u_end]."""
    return not (r_end < u_start or r_start > u_end)


def count_refs(line: str) -> int:
    """Count REF markers on one line (``path:line`` and ``SRC-ID`` forms)."""
    return len(REF_RE.findall(line)) + len(SRC_REF_RE.findall(line))


def validate_ref_range(ref_start: int, ref_end: int,
                       total_lines: int | None = None) -> list[str]:
    """Validate a path REF line range ``[ref_start, ref_end]`` (Issue #381 / SB-10).

    Returns a list of diagnostic strings; empty means the range is usable.

    Checks (in order):
      - ``start >= 1`` (line numbers are 1-indexed; 0 means unparsed/invalid).
      - ``start <= end`` (a reversed range ``10-5`` is never valid).
      - ``end <= EOF`` when *total_lines* is provided (an end beyond the file's
        last line makes the clickable range bogus even though the REF format and
        source-map overlap are fine).
    """
    diag: list[str] = []
    if ref_start < 1:
        diag.append(f"ref start {ref_start} is not a positive line number")
    if ref_start > ref_end:
        diag.append(f"ref range {ref_start}-{ref_end} is reversed (start > end)")
    if total_lines is not None and ref_end > total_lines:
        diag.append(
            f"ref {ref_start}-{ref_end} exceeds EOF ({total_lines} lines)")
    return diag


def validate_path_ref(ref_path: str, ref_start: int, ref_end: int,
                      project_root: str | Path | None = None) -> list[str]:
    """Validate a path REF against the referenced file (Issue #381 / SB-10).

    Extends :func:`validate_ref_range` with file existence: when *project_root*
    is given, the ref path is expected to resolve under it, and the diagnostic
    lists a missing file.  *total_lines* for EOF checking is read from the file
    (when it exists).  SRC-ID refs are not passed here (they carry no path/range).
    """
    diag = validate_ref_range(ref_start, ref_end)
    if not ref_path:
        diag.append("ref path is empty")
        return diag
    if project_root is None:
        return diag
    abs_path = Path(project_root) / ref_path
    if not abs_path.exists():
        diag.append(f"ref path not found: {ref_path}")
        return diag
    try:
        with open(abs_path, "rb") as fh:
            total_lines = sum(1 for _ in fh)
    except OSError:
        return diag
    diag.extend(validate_ref_range(ref_start, ref_end, total_lines))
    return diag
