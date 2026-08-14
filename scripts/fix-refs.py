#!/usr/bin/env python3
"""
fix-refs.py — Phase 7b: auto-correct <!-- REF: path:line --> markers after code changes.

Reads ``git diff --unified`` (or piped diff), computes per-file line-number
mappings, then scans spec Markdown files and updates stale <!-- REF: ... --> markers.

Usage
-----
    # Dry-run (report only, default)
    python fix-refs.py --specback-dir .specback
    python fix-refs.py --specback-dir .specback --base HEAD
    python fix-refs.py --specback-dir .specback --diff < git-diff-output.txt

    # Apply corrections to spec files
    python fix-refs.py --specback-dir .specback --apply

    # Strict mode: exit 1 if any orphaned REFs remain
    python fix-refs.py --specback-dir .specback --check

    # CI: pipe diff directly, fail on orphans
    git diff -U0 main...HEAD | python fix-refs.py --diff - --check

Dependencies
------------
    Python 3.10+ (stdlib only).

Output
------
    - Dry-run: human-readable report to stdout
    - Apply: modifies .md files in drafts/ or final/ in-place
    - Stderr: progress and warning messages
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
import artifact_io
from common import (
    add_specback_dir_arg,
    sanitize_control,
    utcnow_iso,
    utcnow_iso_z,
)
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git_utils import resolve_base, run_git_diff
from refutils import REF_RE, SRC_REF_RE, find_refs_in_text, index_units_by_path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# Hunk parsing
# ---------------------------------------------------------------------------


def parse_hunks(diff_text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse unified diff text and return hunk offsets per file.

    Returns
    -------
    dict mapping file path → list of hunks, where each hunk is:
        {"old_start": N, "old_count": N, "new_start": N, "new_count": N,
         "lines": [(kind, text), ...]}
    ``lines`` holds the hunk body (``"-"`` removed / ``"+"`` added /
    ``" "`` context); it is empty for header-only hunks (e.g. when a caller
    strips the body). Body lines are what make the old→new mapping exact.
    """
    files: dict[str, list[dict[str, Any]]] = {}
    current_file: str | None = None
    current_hunk: dict[str, Any] | None = None

    for line in diff_text.splitlines():
        # Detect file header: "--- a/path" / "+++ b/path"
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()  # strip "+++ b/"
            if current_file not in files:
                files[current_file] = []
            current_hunk = None
            continue
        if line.startswith("--- a/"):
            continue  # skip, we use +++ as authoritative

        # Hunk header
        m = HUNK_RE.match(line)
        if m and current_file is not None:
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": [],
            }
            files[current_file].append(current_hunk)
            continue

        # Hunk body lines (only meaningful inside a hunk)
        if current_hunk is not None and current_file is not None:
            if line[:1] in ("-", "+", " "):
                current_hunk["lines"].append((line[0], line[1:]))

    return files


def build_line_map(hunks: list[dict[str, Any]]) -> dict[int, int | None]:
    """Build a mapping from old line numbers to new line numbers.

    For each old line N, returns:
    - M (new line number) if the line is preserved/shifted
    - None if the line was deleted

    Uses the hunk body (``-`` / ``+`` / `` `` lines) when available, which
    yields an exact mapping — deleted lines map to None wherever they occur,
    not just at the hunk tail (Issue #249 / F2). Falls back to the header-only
    approximation ("deletions at hunk tail") when body lines are absent.

    Only covers line numbers within hunk ranges.
    Lines outside any hunk are assumed unchanged.
    """
    if not hunks:
        return {}

    # Build old→new mapping line by line within hunk ranges
    line_map: dict[int, int | None] = {}

    for hunk in hunks:
        old_start = hunk["old_start"]
        new_start = hunk["new_start"]
        body = hunk.get("lines")

        if body:
            old_cur = old_start
            new_cur = new_start
            for kind, _text in body:
                if kind == "-":
                    line_map[old_cur] = None
                    old_cur += 1
                elif kind == "+":
                    new_cur += 1
                else:  # " " context — preserved 1:1
                    line_map[old_cur] = new_cur
                    old_cur += 1
                    new_cur += 1
            continue

        # Header-only fallback (approximation):
        # - If old_count == new_count: 1:1 mapping (modified lines)
        # - If old_count > new_count: some lines deleted
        # - If old_count < new_count: some lines added
        # Within the hunk, distribute: the first overlap lines map 1:1,
        # extra old lines → deleted (None).
        overlap = min(hunk["old_count"], hunk["new_count"])
        for i in range(overlap):
            line_map[old_start + i] = new_start + i
        for i in range(overlap, hunk["old_count"]):
            line_map[old_start + i] = None

    return line_map


def apply_line_shift(
    old_line: int,
    old_end_line: int | None,
    line_map: dict[int, int | None],
) -> tuple[int | None, int | None]:
    """Apply line map to a <!-- REF: path:line --> or <!-- REF: path:start-end --> marker.

    Parameters
    ----------
    old_line : int
        Start line from the REF marker.
    old_end_line : int or None
        End line from the REF marker (None for single-line refs).
    line_map : dict[int, int | None]
        Mapping from old to new line numbers.

    Returns
    -------
    (new_line, new_end_line) where None means "orphaned/deleted".

    For ranges, the end line is looked up in the map when present; when the
    end is outside every hunk it inherits the start's delta — in a unified
    diff, a hunk whose start shifted by ``d`` implies every later line also
    shifted by ``d`` (Issue #249 / F3).
    """
    new_start = line_map.get(old_line, old_line)
    if new_start is None:
        # Start deleted → the whole range is orphaned
        return (None, None)
    if old_end_line is None:
        # Single-line reference
        return (new_start, None)

    if old_end_line in line_map:
        new_end = line_map[old_end_line]
        if new_end is None:
            # End deleted → orphaned
            return (None, None)
    else:
        # End outside any hunk: it shifts by the same delta as the start.
        new_end = old_end_line + (new_start - old_line)

    return (new_start, new_end)


# ---------------------------------------------------------------------------
# Spec file scanning
# ---------------------------------------------------------------------------


def find_refs_in_file(
    file_path: Path,
) -> list[dict[str, Any]]:
    """Find all <!-- REF: ... --> markers in a spec file.

    Supports two formats:
    - ``<!-- REF: path:line -->`` — traditional path + line reference
    - ``<!-- REF: SRC-NNNN -->`` — indirect source-unit ID (skipped by auto-fix)

    Returns list of dicts with keys:
    - line_no: 0-indexed line number in the file
    - col_start / col_end: 0-indexed match span within the line
    - ref_path: path from the REF marker (or SRC-ID)
    - ref_start: start line number (0 for SRC-ID)
    - ref_end: end line number (0 for SRC-ID; same as ref_start for single-line)
    - is_src_id: True if this is a SRC-ID reference
    - full_match: the matched text (for replacement)

    The per-marker parsing itself lives in :func:`refutils.find_refs_in_text`
    (Issue #281); this wrapper only handles file I/O error tolerance.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []
    return find_refs_in_text(content)


def load_source_map(specback_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load source-map.json and index units by file path.

    Returns a dict mapping file path → list of units sorted by line_range start.
    Units without a path are skipped. Returns {} if the file is missing or unreadable.

    The raw JSON read is delegated to :func:`artifact_io.load_source_map`
    (Issue #283); the sorted by_path index comes from
    :func:`refutils.index_units_by_path`.
    """
    sm_path = specback_path / "source-map.json"
    if not sm_path.exists():
        return {}
    try:
        data = artifact_io.load_source_map(sm_path)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError):
        return {}
    units = data.get("units", []) if isinstance(data, dict) else data
    return index_units_by_path(units or [])


def classify_migration(
    ref: dict[str, Any],
    units_by_path: dict[str, list[dict[str, Any]]],
) -> str:
    """Classify a REF marker for SRC-ID migration (--migrate-srcid).

    Returns one of:
    - ``"src_id"``: already SRC-ID form, no migration needed
    - ``"exact"``: path:line range exactly matches a unit's line_range → migratable
    - ``"no_source_map"``: file not present in source-map.json → not migratable
    - ``"partial"``: start line falls inside a unit but range differs → converting
      would be inaccurate (would shift the click position) → not migratable
    - ``"range_mismatch"``: file exists but the range matches no unit → not migratable
    """
    if ref.get("is_src_id"):
        return "src_id"
    units = units_by_path.get(ref["ref_path"])
    if not units:
        return "no_source_map"
    start, end = ref["ref_start"], ref["ref_end"]
    for u in units:
        lr = u.get("line_range") or [0, 0]
        if lr[0] == start and lr[1] == end:
            return "exact"
    for u in units:
        lr = u.get("line_range") or [0, 0]
        if lr[0] <= start <= lr[1]:
            return "partial"
    return "range_mismatch"


def find_unit_for_ref(
    ref: dict[str, Any],
    units_by_path: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Return the unit whose line_range exactly matches a REF, else None.

    Refuses to proceed when several units share the same (path, line_range):
    an ambiguous conversion would silently point the REF at the wrong unit.
    """
    units = units_by_path.get(ref["ref_path"])
    if not units:
        return None
    start, end = ref["ref_start"], ref["ref_end"]
    matches = []
    for u in units:
        lr = u.get("line_range") or [0, 0]
        if lr[0] == start and lr[1] == end:
            matches.append(u)
    if len(matches) > 1:
        ids = sorted(str(u.get("id", "?")) for u in matches)
        print(
            f"ERROR: duplicate units for {ref['ref_path']}:{start}-{end}: "
            f"{ids}. Refusing ambiguous migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_ref(
    ref: dict[str, Any],
    *,
    is_src_id: bool | None = None,
) -> str:
    """Format a REF marker as a string.

    ``is_src_id`` forces SRC-ID formatting; when None it is inferred from the
    ``is_src_id`` key (present on scanned refs). The ``SRC-`` prefix of a path
    is never used to infer the form — a file named ``SRC-notes.md`` must stay
    a path:line ref (Issue #249 / F6).
    """
    if is_src_id is None:
        is_src_id = bool(ref.get("is_src_id"))
    if is_src_id:
        return f"<!-- REF: {ref['ref_path']} -->"
    if ref["ref_start"] == ref["ref_end"]:
        return f"<!-- REF: {ref['ref_path']}:{ref['ref_start']} -->"
    return f"<!-- REF: {ref['ref_path']}:{ref['ref_start']}-{ref['ref_end']} -->"


def replace_at(
    content: str,
    line_no: int,
    col_start: int,
    col_end: int,
    new_text: str,
    expected: str,
) -> str:
    """Replace the exact span (line_no, col_start..col_end) with new_text.

    Unlike ``str.replace(..., 1)`` this targets the position recorded by the
    scanner, not the first occurrence of the same text elsewhere in the file.
    Refuses to proceed if the span no longer holds ``expected`` (guards against
    the file changing between scan and apply).
    """
    lines = content.splitlines(keepends=True)
    if line_no >= len(lines):
        raise RuntimeError(f"line {line_no + 1} no longer exists in the file")
    line = lines[line_no]
    actual = line[col_start:col_end]
    if actual != expected:
        raise RuntimeError(
            f"REF text mismatch at line {line_no + 1} col {col_start}: "
            f"expected {expected!r}, found {actual!r}"
        )
    lines[line_no] = line[:col_start] + new_text + line[col_end:]
    return "".join(lines)


def _check_writable_spec(spec_path: Path, spec_dir: Path) -> bool:
    """Return False (with a warning) when spec_path is a symlink or outside spec_dir."""
    if not spec_path.exists():
        return False
    if spec_path.is_symlink():
        print(
            f"ERROR: refusing to process symlink: {spec_path}",
            file=sys.stderr,
        )
        return False
    real = spec_path.resolve()
    root = spec_dir.resolve()
    if real != root and root not in real.parents:
        print(
            f"ERROR: refusing to write outside spec dir: {real}",
            file=sys.stderr,
        )
        return False
    return True


def run_migrate_srcid(
    args: argparse.Namespace,
    specback_path: Path,
    output_dir: Path,
    spec_dir: Path,
) -> int:
    """--migrate-srcid: convert path:line REFs to SRC-ID form.

    Only REFs whose path + line range exactly match a source-map unit are
    migrated (safe conversion). REFs that cannot be resolved safely
    (file not in source-map, range outside any unit, or partial overlap)
    are reported and left untouched, since converting them would make the
    click-to-source position inaccurate.

    Dry-run by default; pass --apply to rewrite spec files.
    """
    units_by_path = load_source_map(specback_path)
    if not units_by_path:
        print(
            "fix-refs.py: ERROR: source-map.json has no units (or is missing). "
            "Cannot migrate REFs without a source map.",
            file=sys.stderr,
        )
        return 2

    spec_files = sorted(spec_dir.glob("*.md"))
    all_refs: list[dict[str, Any]] = []
    for spec_file in spec_files:
        refs = find_refs_in_file(spec_file)
        for ref in refs:
            ref["spec_file"] = spec_file.name
        all_refs.extend(refs)

    path_line_refs = [r for r in all_refs if not r.get("is_src_id")]
    src_id_refs = [r for r in all_refs if r.get("is_src_id")]

    migratable: list[dict[str, Any]] = []
    not_migratable: list[dict[str, Any]] = []
    for ref in path_line_refs:
        cls = classify_migration(ref, units_by_path)
        if cls == "exact":
            unit = find_unit_for_ref(ref, units_by_path)
            if unit is None:
                # Should not happen for "exact" classification, but be safe.
                not_migratable.append({**ref, "reason": "range_mismatch"})
                continue
            migratable.append({
                **ref,
                "src_id": unit["id"],
                "new_ref": format_ref(
                    {"ref_path": unit["id"], "ref_start": 0, "ref_end": 0},
                    is_src_id=True,
                ),
            })
        else:
            not_migratable.append({**ref, "reason": cls})

    # Categorize why REFs were not migrated
    reasons: dict[str, int] = {}
    for ref in not_migratable:
        r = ref["reason"]
        reasons[r] = reasons.get(r, 0) + 1

    # -- Report --
    mode = "DRY-RUN" if not args.apply else "APPLY"
    lines: list[str] = [
        f"# [REF] SRC-ID Migration Report ({mode})",
        "",
        f"<!-- auto-generated by fix-refs.py | {utcnow_iso_z()} -->",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Spec files scanned | {len(spec_files)} |",
        f"| <!-- REF: ... --> markers | {len(all_refs)} |",
        f"| — Already SRC-ID (untouched) | {len(src_id_refs)} |",
        f"| — Path:line candidates | {len(path_line_refs)} |",
        f"| **Migratable (exact unit match)** | **{len(migratable)}** |",
        f"| Not migratable — file not in source-map | {reasons.get('no_source_map', 0)} |",
        f"| Not migratable — partial overlap | {reasons.get('partial', 0)} |",
        f"| Not migratable — range mismatch | {reasons.get('range_mismatch', 0)} |",
        "",
    ]

    if migratable:
        lines.append("## Migratable REFs (would convert)")
        lines.append("")
        for m in migratable:
            lines.append(
                f"- `{m['spec_file']}` (line {m['line_no'] + 1}): "
                f"`{sanitize_control(m['full_match'])}` → `{sanitize_control(m['new_ref'])}`"
            )
        lines.append("")

    if not_migratable:
        lines.append("## Not Migratable (kept as path:line)")
        lines.append("")
        lines.append(
            "These REFs cannot be safely converted to SRC-ID because their "
            "path/range does not exactly match a source-map unit. Converting "
            "them would make the click-to-source position inaccurate."
        )
        lines.append("")
        for n in not_migratable:
            reason_label = {
                "no_source_map": "file not in source-map",
                "partial": "partial unit overlap",
                "range_mismatch": "no unit at this range",
            }.get(n["reason"], n["reason"])
            lines.append(
                f"- `{n['spec_file']}` (line {n['line_no'] + 1}): "
                f"`{sanitize_control(n['full_match'])}` — {reason_label}"
            )
        lines.append("")

    report_text = "\n".join(lines)

    # -- Apply conversions --
    if args.apply and migratable:
        backup_dir = Path(args.backup_dir) if args.backup_dir else (
            output_dir / "backups"
        )
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Group by spec file
        by_spec_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for m in migratable:
            by_spec_file[m["spec_file"]].append(m)

        for spec_name, file_migrations in by_spec_file.items():
            spec_path = spec_dir / spec_name
            if not _check_writable_spec(spec_path, spec_dir):
                continue

            content = spec_path.read_text(encoding="utf-8")
            backup_path = backup_dir / (
                f"{spec_name}.{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.bak"
            )
            backup_path.write_text(content, encoding="utf-8")

            # Apply bottom-up; replace at the exact recorded position, not the
            # first occurrence of the same text (Issue #248 / FIX-1).
            sorted_migrations = sorted(
                file_migrations, key=lambda x: -x["line_no"]
            )
            for m in sorted_migrations:
                content = replace_at(
                    content,
                    m["line_no"],
                    m["col_start"],
                    m["col_end"],
                    m["new_ref"],
                    expected=m["full_match"],
                )

            spec_path.write_text(content, encoding="utf-8")
            print(
                f"fix-refs.py: migrated {len(file_migrations)} REFs "
                f"to SRC-ID in {spec_name} (backup: {backup_path})",
                file=sys.stderr,
            )

    # -- Output --
    if args.json:
        json_report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utcnow_iso(),
            "mode": f"migrate-srcid-{mode.lower()}",
            "summary": {
                "spec_files_scanned": len(spec_files),
                "refs_scanned": len(all_refs),
                "refs_already_src_id": len(src_id_refs),
                "refs_path_line": len(path_line_refs),
                "migratable": len(migratable),
                "not_migratable": len(not_migratable),
                "reasons": reasons,
            },
            "migratable": migratable,
            "not_migratable": not_migratable,
        }
        print(json.dumps(json_report, ensure_ascii=False, indent=2))
    else:
        print(report_text)

    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="specback Phase 7b: auto-correct <!-- REF: ... --> line numbers",
    )
    add_specback_dir_arg(p)
    p.add_argument("--output-dir", default=None,
                    help="Base output directory containing drafts/ and final/ "
                         "(default: same as --specback-dir)")
    p.add_argument("--target-dir", default=None,
                    help="Spec directory to scan. "
                         "Accepts a simple name (resolved under --output-dir) or a full path.")
    p.add_argument("--base", default=None,
                    help="Git ref to diff against (default: from state.json)")
    p.add_argument("--diff", default=None,
                    help="Raw git diff -U0 text (for CI use)")
    p.add_argument("--apply", action="store_true",
                    help="Apply corrections (default: dry-run)")
    p.add_argument("--check", action="store_true",
                    help="Exit 1 if any orphaned REFs remain (for CI gates)")
    p.add_argument("--migrate-srcid", action="store_true",
                    help="Migrate path:line REFs that exactly match a source-map "
                         "unit to SRC-ID form (dry-run by default, use --apply)")
    p.add_argument("--backup-dir", default=None,
                    help="Backup directory for originals before apply "
                         "(default: <output-dir>/backups/)")
    p.add_argument("--json", action="store_true",
                    help="Output machine-readable JSON report")
    args = p.parse_args(argv)

    specback_path = Path(args.specback_dir)
    if not specback_path.is_dir():
        print(f"ERROR: {args.specback_dir} is not a directory.", file=sys.stderr)
        return 2

    # --output-dir defaults to --specback-dir if not specified
    output_dir = Path(args.output_dir) if args.output_dir else specback_path

    # -- Determine spec target directory --
    if args.target_dir:
        tdr = args.target_dir
        if "/" in tdr or (os.sep != "/" and os.sep in tdr):
            spec_dir = Path(tdr)
        else:
            spec_dir = output_dir / tdr
    else:
        # Auto-detect: prefer final/ over drafts/ under output_dir
        if (output_dir / "final").is_dir():
            spec_dir = output_dir / "final"
        elif (output_dir / "drafts").is_dir():
            spec_dir = output_dir / "drafts"
        else:
            print(
                f"ERROR: No drafts/ or final/ directory found in {output_dir}.",
                file=sys.stderr,
            )
            return 2

    # -- SRC-ID migration mode (does not need a git diff) --
    if args.migrate_srcid:
        if args.diff is not None or args.base is not None:
            print(
                "ERROR: --migrate-srcid cannot be combined with --diff/--base.",
                file=sys.stderr,
            )
            return 2
        if args.check:
            print(
                "WARNING: --check has no effect with --migrate-srcid.",
                file=sys.stderr,
            )
        return run_migrate_srcid(args, specback_path, output_dir, spec_dir)

    # -- Get diff --
    if args.diff is not None:
        diff_text = args.diff
        base = "stdin"
    else:
        base = resolve_base(args.base, specback_path)
        print(f"fix-refs.py: diffing against --base {base[:12]}",
              file=sys.stderr)
        diff_text = run_git_diff(base, "-U0", cwd=str(specback_path.parent))

    if not diff_text.strip():
        print("fix-refs.py: No changes detected. Nothing to fix.")
        return 0

    # -- Parse hunks --
    hunks_by_file = parse_hunks(diff_text)
    if not hunks_by_file:
        print("fix-refs.py: No hunks found in diff. Nothing to fix.")
        return 0

    print(
        f"fix-refs.py: {len(hunks_by_file)} files with hunks in diff.",
        file=sys.stderr,
    )

    # -- Build line maps --
    line_maps: dict[str, dict[int, int | None]] = {}
    for file_path, hunks in hunks_by_file.items():
        line_maps[file_path] = build_line_map(hunks)

    # -- Scan spec files --
    all_refs: list[dict[str, Any]] = []
    spec_files = sorted(spec_dir.glob("*.md"))
    for spec_file in spec_files:
        refs = find_refs_in_file(spec_file)
        for ref in refs:
            ref["spec_file"] = spec_file.name
        all_refs.extend(refs)

    print(
        f"fix-refs.py: found {len(all_refs)} <!-- REF: ... --> markers in "
        f"{len(spec_files)} spec files.",
        file=sys.stderr,
    )

    # -- Evaluate corrections --
    corrections: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    src_id_refs: list[dict[str, Any]] = []

    for ref in all_refs:
        if ref.get("is_src_id"):
            # SRC-ID refs have no line numbers — auto-stable, skip correction
            src_id_refs.append(ref)
            continue

        rel_path = ref["ref_path"]
        lm = line_maps.get(rel_path)

        if lm is None:
            # File not in diff — REF is unchanged
            unchanged.append(ref)
            continue

        new_start, new_end = apply_line_shift(
            ref["ref_start"], ref["ref_end"], lm,
        )

        if new_start is None:
            # Line was deleted
            orphaned.append(ref)
        elif new_start != ref["ref_start"] or (
            new_end is not None and new_end != ref["ref_end"]
        ):
            # Line number changed
            corrections.append({
                **ref,
                "old_start": ref["ref_start"],
                "old_end": ref["ref_end"],
                "new_start": new_start,
                "new_end": new_end if new_end is not None else new_start,
            })
        else:
            unchanged.append(ref)

    # -- Report --
    total_issues = len(corrections) + len(orphaned)
    mode = "DRY-RUN" if not args.apply else "APPLY"
    lines: list[str] = [
        f"# [REF] Fix Report ({mode})",
        "",
        f"<!-- auto-generated by fix-refs.py | {utcnow_iso_z()} -->",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| Files with hunks | {len(hunks_by_file)} |",
        f"| <!-- REF: ... --> markers scanned | {len(all_refs)} |",
        f"| — Path:line format | {len(corrections) + len(orphaned) + len(unchanged)} |",
        f"| — SRC-ID format (auto-stable) | {len(src_id_refs)} |",
        f"| **Line corrections needed** | **{len(corrections)}** |",
        f"| **Orphaned REFs (deleted)** | **{len(orphaned)}** |",
        f"| Unchanged | {len(unchanged)} |",
        "",
    ]

    if corrections:
        lines.append("## Corrections (Line Number Shifts)")
        lines.append("")
        for c in corrections:
            old = f"{c['old_start']}" + (
                f"-{c['old_end']}" if c['old_end'] != c['old_start'] else ""
            )
            new = f"{c['new_start']}" + (
                f"-{c['new_end']}" if c['new_end'] != c['new_start'] else ""
            )
            lines.append(
                f"- `{c['spec_file']}`: "
                f"`<!-- REF: {sanitize_control(c['ref_path'])}:{old} -->` → `<!-- REF: {sanitize_control(c['ref_path'])}:{new} -->`"
                f"  (line {c['line_no'] + 1})"
            )
        lines.append("")

    if orphaned:
        lines.append("## Orphaned REFs (Deleted Source Lines)")
        lines.append("")
        lines.append(
            "These <!-- REF: ... --> markers reference source lines that no longer "
            "exist. Consider marking them with `[DEPRECATED]`."
        )
        lines.append("")
        for o in orphaned:
            old = f"{o['ref_start']}" + (
                f"-{o['ref_end']}" if o['ref_end'] != o['ref_start'] else ""
            )
            lines.append(
                f"- `{o['spec_file']}`: "
                f"`<!-- REF: {sanitize_control(o['ref_path'])}:{old} -->` (line {o['line_no'] + 1})"
            )
        lines.append("")

    if not total_issues:
        lines.append("No corrections needed. All <!-- REF: ... --> markers are up to date.")
        lines.append("")

    report_text = "\n".join(lines)

    # -- Apply corrections --
    if args.apply and corrections:
        backup_dir = Path(args.backup_dir) if args.backup_dir else (
            output_dir / "backups"
        )
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Group corrections by spec file
        by_spec_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in corrections:
            by_spec_file[c["spec_file"]].append(c)

        for spec_name, file_corrections in by_spec_file.items():
            spec_path = spec_dir / spec_name
            if not _check_writable_spec(spec_path, spec_dir):
                continue

            content = spec_path.read_text(encoding="utf-8")
            backup_path = backup_dir / (
                f"{spec_name}.{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.bak"
            )
            backup_path.write_text(content, encoding="utf-8")

            # Apply corrections in reverse line order (bottom-up to avoid offset
            # issues); replace at the exact recorded position, not the first
            # occurrence of the same text (Issue #248 / FIX-1).
            sorted_corrections = sorted(
                file_corrections, key=lambda x: -x["line_no"]
            )
            for c in sorted_corrections:
                new_ref = format_ref({
                    "ref_path": c["ref_path"],
                    "ref_start": c["new_start"],
                    "ref_end": c["new_end"],
                })
                content = replace_at(
                    content,
                    c["line_no"],
                    c["col_start"],
                    c["col_end"],
                    new_ref,
                    expected=c["full_match"],
                )

            spec_path.write_text(content, encoding="utf-8")
            print(
                f"fix-refs.py: applied {len(file_corrections)} corrections "
                f"to {spec_name} (backup: {backup_path})",
                file=sys.stderr,
            )

        mode_str = "applied" if args.apply else "would apply"
        print(
            f"fix-refs.py: {mode_str} {len(corrections)} line corrections, "
            f"{len(orphaned)} orphan(s) need manual review",
            file=sys.stderr,
        )

    # -- Output --
    if args.json:
        json_report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utcnow_iso(),
            "base": base,
            "mode": mode,
            "summary": {
                "files_with_hunks": len(hunks_by_file),
                "refs_scanned": len(all_refs),
                "refs_path_line": len(corrections) + len(orphaned) + len(unchanged),
                "refs_src_id": len(src_id_refs),
                "corrections_applied": len(corrections),
                "orphaned_refs": len(orphaned),
                "unchanged_refs": len(unchanged),
            },
            "corrections": corrections,
            "orphaned": orphaned,
        }
        print(json.dumps(json_report, ensure_ascii=False, indent=2))
    else:
        print(report_text)

    # -- Check mode --
    if args.check and orphaned:
        print("fix-refs.py: CHECK FAILED — orphaned REFs remain.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
