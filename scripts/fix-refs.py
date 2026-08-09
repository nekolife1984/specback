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
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"
REF_RE = re.compile(r"<!-- REF:\s*([^:\]]+):(\d+)(?:-(\d+))?\s*-->")
SRC_REF_RE = re.compile(r"<!-- REF:\s*(SRC-\d+)\s*-->")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# Hunk parsing
# ---------------------------------------------------------------------------


def parse_hunks(diff_text: str) -> dict[str, list[dict[str, int]]]:
    """Parse unified diff text and return hunk offsets per file.

    Returns
    -------
    dict mapping file path → list of hunks, where each hunk is:
        {"old_start": N, "old_count": N, "new_start": N, "new_count": N}
    """
    files: dict[str, list[dict[str, int]]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        # Detect file header: "--- a/path" / "+++ b/path"
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()  # strip "+++ b/"
            if current_file not in files:
                files[current_file] = []
            continue
        if line.startswith("--- a/"):
            continue  # skip, we use +++ as authoritative

        # Hunk header
        if current_file is not None:
            m = HUNK_RE.match(line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                files[current_file].append({
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                })

    return files


def build_line_map(hunks: list[dict[str, int]]) -> dict[int, int | None]:
    """Build a mapping from old line numbers to new line numbers.

    For each old line N, returns:
    - M (new line number) if the line is preserved/shifted
    - None if the line was deleted

    Only covers line numbers within hunk ranges.
    Lines outside any hunk are assumed unchanged.
    """
    if not hunks:
        return {}

    # Build old→new mapping line by line within hunk ranges
    line_map: dict[int, int | None] = {}

    for hunk in hunks:
        old_start = hunk["old_start"]
        old_count = hunk["old_count"]
        new_start = hunk["new_start"]
        new_count = hunk["new_count"]

        old_end = old_start + old_count - 1
        new_cursor = new_start

        # In a unified diff, the hunk contains both old and new lines.
        # We need the full diff context to map precisely. For -U0 diffs,
        # each line is either removed (only in old) or added (only in new).
        # Since we don't have the full diff body here, we use an approximation:
        # - If old_count == new_count: 1:1 mapping (modified lines)
        # - If old_count > new_count: some lines deleted
        # - If old_count < new_count: some lines added
        #
        # Within the hunk, we distribute:
        #   overlap = min(old_count, new_count)
        #   first overlap lines map 1:1
        #   extra old lines → deleted (None)
        #   extra new lines = insertion (no old source)
        overlap = min(old_count, new_count)
        for i in range(overlap):
            line_map[old_start + i] = new_cursor + i
        # Deleted lines
        for i in range(overlap, old_count):
            line_map[old_start + i] = None
        # Note: new_cursor + overlap is the first insertion point

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
    """
    new_start = line_map.get(old_line, old_line)
    if old_end_line is None:
        # Single-line reference
        return (new_start, None)

    # For ranges, look up the end. If not in map, it's unchanged.
    new_end = line_map.get(old_end_line, old_end_line)

    # If either start or end is deleted, the range is orphaned
    if new_start is None or new_end is None:
        return (None, None)

    # Both lines are preserved. Shift each independently per the map.
    # If only the start shifted but the end wasn't affected by any hunk,
    # the range length changes naturally (correct behavior — the end
    # genuinely didn't move).

    return (new_start, new_end)


# ---------------------------------------------------------------------------
# Git diff helper
# ---------------------------------------------------------------------------


def get_git_diff(
    base: str,
    cwd: str | Path | None = None,
) -> str:
    """Run ``git diff -U0 <base>`` and return the diff text."""
    cmd = ["git", "diff", "-U0", base]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    if result.returncode != 0:
        print(
            f"ERROR: git diff failed:\n{result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    return result.stdout


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
    - ref_path: path from the REF marker (or SRC-ID)
    - ref_start: start line number (0 for SRC-ID)
    - ref_end: end line number (0 for SRC-ID; same as ref_start for single-line)
    - is_src_id: True if this is a SRC-ID reference
    - full_match: the matched text (for replacement)
    - prefix: text before the REF marker on the line
    - suffix: text after the REF marker on the line
    """
    refs: list[dict[str, Any]] = []
    try:
        content = file_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return refs

    for line_no_0idx, line in enumerate(content.splitlines()):
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


def load_state(path: Path) -> dict[str, Any] | None:
    """Load state.json if it exists."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_source_map(specback_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load source-map.json and index units by file path.

    Returns a dict mapping file path → list of units sorted by line_range start.
    Units without a path are skipped. Returns {} if the file is missing or unreadable.
    """
    sm_path = specback_path / "source-map.json"
    if not sm_path.exists():
        return {}
    try:
        data = json.loads(sm_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    units = data.get("units", []) if isinstance(data, dict) else data
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in units or []:
        if isinstance(u, dict) and u.get("path") is not None:
            by_path[u["path"]].append(u)
    for p in by_path:
        by_path[p].sort(key=lambda u: (u.get("line_range") or [0, 0])[0])
    return by_path


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
    """Return the unit whose line_range exactly matches a REF, else None."""
    units = units_by_path.get(ref["ref_path"])
    if not units:
        return None
    start, end = ref["ref_start"], ref["ref_end"]
    for u in units:
        lr = u.get("line_range") or [0, 0]
        if lr[0] == start and lr[1] == end:
            return u
    return None


def resolve_base(args_base: str | None, specback_path: Path) -> str:
    """Determine the git ref to diff against (same logic as detect-drift.py)."""
    if args_base is not None:
        return args_base
    state = load_state(specback_path / "state.json")
    if state is not None:
        commit = state.get("generated_at_commit")
        if commit:
            return str(commit)
    return "HEAD"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_ref(ref: dict[str, Any]) -> str:
    """Format a REF marker as a string."""
    # SRC-ID refs have no line numbers
    if str(ref.get("ref_path", "")).startswith("SRC-"):
        return f"<!-- REF: {ref['ref_path']} -->"
    if ref["ref_start"] == ref["ref_end"]:
        return f"<!-- REF: {ref['ref_path']}:{ref['ref_start']} -->"
    return f"<!-- REF: {ref['ref_path']}:{ref['ref_start']}-{ref['ref_end']} -->"


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
                "new_ref": format_ref({"ref_path": unit["id"]}),
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
        f"<!-- auto-generated by fix-refs.py | {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->",
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
                f"`{m['full_match']}` → `{m['new_ref']}`"
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
                f"`{n['full_match']}` — {reason_label}"
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
            if not spec_path.exists():
                continue

            content = spec_path.read_text(encoding="utf-8")
            backup_path = backup_dir / f"{spec_name}.bak"
            backup_path.write_text(content, encoding="utf-8")

            # Apply bottom-up to avoid offset issues
            sorted_migrations = sorted(
                file_migrations, key=lambda x: -x["line_no"]
            )
            for m in sorted_migrations:
                content = content.replace(m["full_match"], m["new_ref"], 1)

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
            "generated_at": datetime.now(timezone.utc).isoformat(),
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
    p.add_argument("--specback-dir", default=".specback",
                    help="Path to .specback/ state directory")
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
        return run_migrate_srcid(args, specback_path, output_dir, spec_dir)

    # -- Get diff --
    if args.diff is not None:
        diff_text = args.diff
        base = "stdin"
    else:
        base = resolve_base(args.base, specback_path)
        print(f"fix-refs.py: diffing against --base {base[:12]}",
              file=sys.stderr)
        diff_text = get_git_diff(base, cwd=str(specback_path.parent))

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
        f"<!-- auto-generated by fix-refs.py | {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->",
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
                f"`<!-- REF: {c['ref_path']}:{old} -->` → `<!-- REF: {c['ref_path']}:{new} -->`"
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
                f"`<!-- REF: {o['ref_path']}:{old} -->` (line {o['line_no'] + 1})"
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
            if not spec_path.exists():
                continue

            content = spec_path.read_text(encoding="utf-8")
            backup_path = backup_dir / f"{spec_name}.bak"
            backup_path.write_text(content, encoding="utf-8")

            # Apply corrections in reverse line order (bottom-up to avoid offset issues)
            sorted_corrections = sorted(
                file_corrections, key=lambda x: -x["line_no"]
            )
            for c in sorted_corrections:
                old_ref = format_ref({
                    "ref_path": c["ref_path"],
                    "ref_start": c["old_start"],
                    "ref_end": c["old_end"],
                })
                new_ref = format_ref({
                    "ref_path": c["ref_path"],
                    "ref_start": c["new_start"],
                    "ref_end": c["new_end"],
                })
                content = content.replace(old_ref, new_ref, 1)

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
            "generated_at": datetime.now(timezone.utc).isoformat(),
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
