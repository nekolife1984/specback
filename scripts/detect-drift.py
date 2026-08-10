#!/usr/bin/env python3
"""
detect-drift.py — detect spec drift from source code changes.

Reads ``git diff`` (git mode) or compares SRC-ID hashes (hash mode)
and cross-references with source-map.json and trace.json to identify
which spec sections are potentially affected by source changes.

This is the core script for specback Phase 7 (Drift Detection).

Usage
-----
    # Git mode (default when Git repo available)
    python detect-drift.py --specback-dir .specback
    python detect-drift.py --specback-dir .specback --base v1.0
    python detect-drift.py --specback-dir .specback --diff < git-diff-output.txt

    # Hash mode (for non-Git projects)
    python detect-drift.py --specback-dir .specback --mode hash

    # Explicit mode selection
    python detect-drift.py --specback-dir .specback --mode git
    python detect-drift.py --specback-dir .specback --mode auto

Dependencies
------------
- Python 3.10+
- git (when using git mode without ``--diff``)
- No external PyPI packages (stdlib only).

Output
------
- <output-dir>/drift-report.md    (human-readable Markdown)
- <output-dir>/drift-report.json  (machine-readable)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git_utils import resolve_ref


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"
CHANGE_TYPES = {"A": "Added", "C": "Copied", "D": "Deleted", "M": "Modified",
                "R": "Renamed", "T": "Type-Change", "U": "Unmerged"}

# Detection modes
MODE_AUTO = "auto"
MODE_GIT = "git"
MODE_HASH = "hash"

# Impact level heuristic
IMPACT_HIGH = "high"     # DELETE of a file that has spec refs / ADD of new source
IMPACT_MODERATE = "moderate"  # MODIFY of a file with spec refs
IMPACT_LOW = "low"       # MODIFY of a file with no spec refs but source-map entry
IMPACT_NONE = "none"     # Change that maps to nothing


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git_diff_name_status(
    base: str,
    cwd: str | Path | None = None,
) -> list[dict[str, str]]:
    """Run ``git diff --name-status <base>`` and return parsed entries.

    Returns a list of ``{"status": "M", "file": "path/to/file"}``.
    """
    # Resolve base to a validated commit hash before building argv —
    # prevents git option injection via --base / state.json (Issue #253).
    base = resolve_ref(base, cwd)
    cmd = ["git", "diff", "--name-status", base]
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

    entries: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]  # first char: A/M/D/R/...
        if status == "R" and len(parts) >= 3:
            # R<similarity>\told/path\tnew/path
            entries.append({
                "status": status,
                "file": parts[2],          # new path
                "old_file": parts[1],       # old path
            })
        else:
            file_path = parts[1]
            entries.append({"status": status, "file": file_path})
    return entries


def parse_diff_text(text: str) -> list[dict[str, str]]:
    """Parse ``--name-status`` format text passed inline.

    Handles all git status codes, including rename (R) which produces
    three tab-separated fields: status, old_path, new_path.
    """
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]
        if status == "R" and len(parts) >= 3:
            # R<similarity>\told/path\tnew/path
            entries.append({
                "status": status,
                "file": parts[2],          # new path for source-map lookup
                "old_file": parts[1],       # old path for orphaned-REF detection
            })
        else:
            file_path = parts[1]
            entries.append({"status": status, "file": file_path})
    return entries


# ---------------------------------------------------------------------------
# Load artifacts
# ---------------------------------------------------------------------------

def load_source_map(path: Path) -> dict[str, Any]:
    """Load source-map.json and build two indexes:

    - by_path: path → list[SRC unit dict]
    - by_id: SRC-ID → unit dict
    """
    if not path.exists():
        print(
            f"ERROR: source-map.json not found at {path}. "
            "Run scripts/source-map.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    units = data.get("units", [])

    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[str, dict[str, Any]] = {}

    for unit in units:
        uid = unit.get("id", "")
        u_path = unit.get("path", "")
        by_path[u_path].append(unit)
        by_id[uid] = unit

    return {
        "units": units,
        "by_path": dict(by_path),
        "by_id": by_id,
        "stats": data.get("stats", {}),
        "target_root": data.get("target_root", ""),
    }


def load_trace(path: Path) -> dict[str, Any]:
    """Load trace.json."""
    if not path.exists():
        print(
            f"ERROR: trace.json not found at {path}. "
            "Run scripts/build-trace.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_state(path: Path) -> dict[str, Any] | None:
    """Load state.json if it exists (silently return None otherwise)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_source_hashes(path: Path) -> dict[str, Any]:
    """Load source-hashes.json (exit with error if missing)."""
    if not path.exists():
        print(
            f"ERROR: {path} not found. Run scripts/snapshot-hashes.py first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Hash comparison helpers
# ---------------------------------------------------------------------------

def hash_line_range(
    file_path: Path,
    line_start: int,
    line_end: int,
) -> tuple[str, int]:
    """Compute SHA256 of lines [line_start, line_end] (1-indexed inclusive).

    Returns (hex_digest, actual_line_count).
    Normalizes text encoding to eliminate nondeterminism:
    - Reads as UTF-8 (strips BOM if present)
    - Treats CRLF and LF as equivalent (rstrip trailing newline chars)
    - Line-level trailing content (whitespace) is *preserved* — only
      the line-ending character (\\n, \\r\\n) is stripped for hashing.
    """
    hasher = hashlib.sha256()
    line_count = 0
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            for current_lineno, line in enumerate(f, start=1):
                if current_lineno > line_end:
                    break
                if current_lineno >= line_start:
                    normalized = line.rstrip("\n\r")
                    hasher.update(normalized.encode("utf-8"))
                    line_count += 1
    except (FileNotFoundError, OSError):
        return "", 0
    return hasher.hexdigest(), line_count


def compute_hash_changes(
    source_hashes: dict[str, Any],
    source_map: dict[str, Any],
    target_root: str,
) -> list[dict[str, str]]:
    """Compare current file content against stored SRC-ID hashes.

    Returns a list of changes in the same format as parse_diff_text/git diff:
    ``[{"status": "M"|"D"|"A", "file": "..."}]``
    """
    changes: list[dict[str, str]] = []
    units = source_hashes.get("units", {})
    seen_paths: set[str] = set()

    for sid, stored in units.items():
        file_path_rel = stored.get("path", "")
        line_range = stored.get("line_range", [0, 0])
        stored_hash = stored.get("hash", "")
        stored_line_count = stored.get("line_count", 0)
        status = stored.get("status", "OK")

        seen_paths.add(file_path_rel)
        abs_path = Path(target_root) / file_path_rel

        if status == "MISSING" or not abs_path.exists():
            # Was missing at snapshot time, or deleted since
            changes.append({"status": "D", "file": file_path_rel})
            continue

        # Compute current hash of the line range
        current_digest, current_line_count = hash_line_range(
            abs_path, line_range[0], line_range[1],
        )
        current_full_hash = f"sha256:{current_digest}"

        if stored_hash != current_full_hash:
            # Content changed
            changes.append({"status": "M", "file": file_path_rel})
        elif stored_line_count != current_line_count:
            # Hash same but line count changed (rare edge case)
            changes.append({"status": "M", "file": file_path_rel})

    # Detect new files: scan files matching source-map paths for new entries
    sm_units = source_map.get("units", [])
    sm_paths: set[str] = {u.get("path", "") for u in sm_units if u.get("path")}

    # Files in source-map but NOT in hash snapshot = ADD (new since snapshot)
    for sm_path in sm_paths:
        if sm_path not in seen_paths:
            changes.append({"status": "A", "file": sm_path})

    return changes


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------

def analyze_impact(
    changes: list[dict[str, str]],
    source_map: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Cross-reference each change against source-map and trace.

    Returns a structured dict suitable for JSON serialisation.
    """
    by_path = source_map["by_path"]
    by_id = source_map["by_id"]
    by_source = trace.get("by_source", {})

    affected_sections: list[dict[str, Any]] = []
    new_uncovered: list[dict[str, str]] = []
    deleted_with_refs: list[dict[str, Any]] = []
    no_impact: list[dict[str, str]] = []

    section_keys_seen: set[str] = set()

    for change in changes:
        file = change["file"]
        status = change["status"]
        entry: dict[str, Any] = {"file": file, "status": status}

        # --- Rename: also check old_file for orphaned refs ---
        old_file = change.get("old_file")
        if old_file and status == "R":
            unmatched_renamed = _check_deleted_path_in_trace(old_file, trace)
            if unmatched_renamed:
                deleted_with_refs.append(unmatched_renamed)
            else:
                no_impact.append({
                    "file": old_file,
                    "status": "D",
                    "reason": f"Renamed from {old_file} (old path has no trace coverage)",
                })

        # --- Look up in source-map ---
        src_units = by_path.get(file, [])

        if not src_units:
            # File not in source-map (may be new or excluded)
            if status in ("A", "C", "R"):
                new_uncovered.append({
                    "file": file,
                    "status": status,
                    "reason": "New file not present in source-map.json. "
                              "Refresh source-map to capture this source unit.",
                })
            elif status == "D":
                # Deleted file that wasn't in source-map — check trace
                # by_source for path-only references (no SRC-ID needed)
                unmatched_deleted = _check_deleted_path_in_trace(file, trace)
                if unmatched_deleted:
                    deleted_with_refs.append(unmatched_deleted)
                else:
                    no_impact.append({"file": file, "status": status,
                                      "reason": "Not in source-map or trace"})
            else:
                no_impact.append({"file": file, "status": status,
                                  "reason": "Not in source-map"})
            continue

        # --- File is in source-map: collect SRC-IDs ---
        src_ids = [u["id"] for u in src_units]
        entry["src_ids"] = src_ids

        impacted_sections: list[dict[str, str]] = []
        has_trace_entry = False

        for sid in src_ids:
            trace_entry = by_source.get(sid)
            if not trace_entry:
                continue
            has_trace_entry = True
            sections = trace_entry.get("covered_by_sections", [])
            for sec in sections:
                sec_file = sec.get("file", "")
                sec_section = sec.get("section", "")
                key = f"{sec_file}::{sec_section}"
                section_keys_seen.add(key)

                impact_level = _determine_impact(status, trace_entry, by_id.get(sid, {}))

                impacted_sections.append({
                    "file": sec_file,
                    "section": sec_section,
                    "impact": impact_level,
                })

        entry["impacted_sections"] = impacted_sections

        if status == "D" and not has_trace_entry:
            # Deleted from source-map but no trace coverage
            no_impact.append({"file": file, "status": status,
                              "reason": "Deleted, no spec coverage"})
        elif status == "D" and impacted_sections:
            deleted_with_refs.append(entry)
        elif impacted_sections:
            affected_sections.append(entry)
        elif not impacted_sections and has_trace_entry:
            # Has trace entry but no covered_by_sections (uncovered)
            affected_sections.append(entry)
        else:
            no_impact.append({"file": file, "status": status,
                              "reason": "In source-map but no trace coverage"})

    return {
        "affected_sections": affected_sections,
        "new_uncovered": new_uncovered,
        "deleted_with_refs": deleted_with_refs,
        "no_impact": no_impact,
        "section_keys_seen": sorted(section_keys_seen),
    }


def _determine_impact(status: str, trace_entry: dict, src_unit: dict) -> str:
    """Determine impact level for a change on a trace-mapped source unit.

    Heuristics:
    - DELETE → always HIGH (spec ref is now orphaned)
    - ADD → HIGH (new source, no spec coverage yet)
    - MODIFY → MODERATE (spec refs may need line number updates)
    - RENAME → MODERATE (file path in REF markers needs update)
    - COPY → LOW (no existing refs to break)
    - Type-Change → MODERATE
    """
    if status in ("D",):
        return IMPACT_HIGH
    if status in ("A",):
        return IMPACT_HIGH
    if status in ("M",):
        return IMPACT_MODERATE
    if status in ("R",):
        return IMPACT_MODERATE
    if status == "T":
        return IMPACT_MODERATE
    return IMPACT_LOW


def _check_deleted_path_in_trace(
    file: str, trace: dict[str, Any],
) -> dict[str, Any] | None:
    """Check if a deleted file (not in source-map) has any REF references
    in trace.json by_source entries (matched by path suffix)."""
    by_source = trace.get("by_source", {})
    matched_sections = []
    for sid, entry in by_source.items():
        trace_path = entry.get("path", "")
        # Match by filename or suffix (same heuristic as build-trace.py)
        if trace_path.endswith("/" + file) or file.endswith("/" + trace_path) \
                or trace_path == file:
            sections = entry.get("covered_by_sections", [])
            matched_sections.extend(sections)

    if matched_sections:
        return {
            "file": file,
            "status": "D",
            "matched_by_path": True,
            "impacted_sections": [
                {"file": s.get("file", ""), "section": s.get("section", "")}
                for s in matched_sections
            ],
        }
    return None


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def generate_markdown(
    result: dict[str, Any],
    base: str,
    changed_files_count: int,
    specback_dir: str,
) -> str:
    """Generate human-readable Markdown drift report."""
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines.append("# Drift Report")
    lines.append("")
    lines.append(f"<!-- auto-generated by detect-drift.py | {ts} -->")
    lines.append("")
    lines.append(f"- **Generated**: {ts}")
    lines.append(f"- **Base**: `{base}`")
    lines.append(f"- **Changed files**: {changed_files_count}")
    lines.append("")

    # --- Summary ---
    affected = result.get("affected_sections", [])
    new_uncovered = result.get("new_uncovered", [])
    deleted = result.get("deleted_with_refs", [])
    no_impact = result.get("no_impact", [])

    # Count unique spec sections
    all_sections: set[str] = set()
    for entry in affected:
        for sec in entry.get("impacted_sections", []):
            all_sections.add(f"{sec['file']} §{sec['section']}")
        if not entry.get("impacted_sections"):
            # Has SRC-IDs but no coverage — flag as uncovered
            for sid in entry.get("src_ids", []):
                all_sections.add(f"(uncovered SRC) {sid}")
    for entry in deleted:
        for sec in entry.get("impacted_sections", []):
            all_sections.add(f"{sec['file']} §{sec['section']}")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|------:|")
    lines.append(f"| Changed files | {changed_files_count} |")
    lines.append(f"| **Affected spec sections** | **{len(all_sections)}** |")
    # Count high-impact
    high_count = sum(
        1 for e in affected
        for s in e.get("impacted_sections", [])
        if s.get("impact") == "high"
    )
    high_count += sum(
        1 for e in deleted
        for s in e.get("impacted_sections", [])
    )
    moderate_count = sum(
        1 for e in affected
        for s in e.get("impacted_sections", [])
        if s.get("impact") == "moderate"
    )
    lines.append(f"| 🔴 High impact | {high_count} |")
    lines.append(f"| 🟡 Moderate impact | {moderate_count} |")
    lines.append(f"| 🆕 New uncovered sources | {len(new_uncovered)} |")
    lines.append(f"| 🗑️ Deleted with spec refs | {len(deleted)} |")
    lines.append(f"| ⚪ No impact | {len(no_impact)} |")
    lines.append("")

    # --- Impact level legend ---
    lines.append("## Impact Level Legend")
    lines.append("")
    lines.append("| Level | Meaning |")
    lines.append("|-------|---------|")
    lines.append("| 🔴 **high** | DELETE of referenced file, or ADD of new source — spec update required |")
    lines.append("| 🟡 **moderate** | MODIFY of referenced file — REF line numbers may be stale, content may have changed |")
    lines.append("| 🟢 **low** | Minor change (rename, copy) — verify but unlikely to break spec |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Affected spec sections (modified files with coverage) ---
    if affected:
        lines.append("## Affected Spec Sections")
        lines.append("")
        lines.append("These spec sections reference source files that have changed.")
        lines.append("")

        # Group by spec file
        by_spec_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in affected:
            for sec in entry.get("impacted_sections", []):
                spec_file = sec.get("file", "?")
                by_spec_file[spec_file].append({
                    "section": sec.get("section", "?"),
                    "impact": sec.get("impact", "none"),
                    "source_file": entry["file"],
                    "status": CHANGE_TYPES.get(entry["status"], entry["status"]),
                    "src_ids": entry.get("src_ids", []),
                })

        for spec_file in sorted(by_spec_file):
            sections_in_file = by_spec_file[spec_file]
            lines.append(f"### `{spec_file}`")
            lines.append("")
            # Group by section
            by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for s in sections_in_file:
                by_section[s["section"]].append(s)

            for section_name in sorted(by_section):
                sources = by_section[section_name]
                max_impact = max(s["impact"] for s in sources)
                impact_icon = {"high": "🔴", "moderate": "🟡", "low": "🟢", "none": "⚪"}
                icon = impact_icon.get(max_impact, "⚪")

                lines.append(f"#### {icon} {section_name}")
                lines.append("")
                for src in sources:
                    src_ids_str = ", ".join(src["src_ids"][:5])
                    more = f" and {len(src['src_ids'])-5} more" if len(src['src_ids']) > 5 else ""
                    lines.append(
                        f"- **{src['source_file']}** ({src['status']}) "
                        f"— {src['impact']} impact"
                        f" | SRC: {src_ids_str}{more}"
                    )
                lines.append("")
        lines.append("---")
        lines.append("")

    # --- Deleted files with spec refs ---
    if deleted:
        lines.append("## 🗑️ Deleted Files with Spec References")
        lines.append("")
        lines.append(
            "These files have been deleted but are referenced in one or more "
            "spec sections. The corresponding `<!-- REF: ... -->` markers are orphaned."
        )
        lines.append("")
        for entry in deleted:
            lines.append(f"### `{entry['file']}`")
            lines.append("")
            for sec in entry.get("impacted_sections", []):
                lines.append(
                    f"- Referenced in `{sec['file']}` §{sec['section']} — "
                    f"mark as `[DEPRECATED]` or remove the reference"
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    # --- New uncovered sources ---
    if new_uncovered:
        lines.append("## 🆕 New Uncovered Sources")
        lines.append("")
        lines.append(
            "These files have been added to the codebase but are not yet "
            "captured in the source map or spec. They are candidates for "
            "new spec sections or REF additions."
        )
        lines.append("")
        lines.append("| File | Status | Note |")
        lines.append("|------|--------|------|")
        for entry in new_uncovered:
            lines.append(
                f"| `{entry['file']}` | {CHANGE_TYPES.get(entry['status'], entry['status'])} "
                f"| {entry.get('reason', '')} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- No-impact files ---
    if no_impact:
        lines.append("## ⚪ No-Impact Changes")
        lines.append("")
        lines.append("These file changes do not map to any spec section.")
        lines.append("")
        lines.append("| File | Status | Reason |")
        lines.append("|------|--------|--------|")
        for entry in no_impact:
            lines.append(
                f"| `{entry['file']}` | {CHANGE_TYPES.get(entry['status'], entry['status'])} "
                f"| {entry.get('reason', '')} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- Recommendations ---
    lines.append("## Recommendations")
    lines.append("")
    total_affected = len(affected) + len(deleted) + len(new_uncovered)
    if total_affected == 0:
        lines.append("No spec sections are affected by the current changes.")
        lines.append("The spec is up to date.")
    else:
        lines.append("Based on the drift analysis, the following actions are recommended:")
        lines.append("")

        if deleted:
            lines.append(
                "1. **🔴 Fix orphaned REFs**: "
                "Update or remove `<!-- REF: ... -->` markers in specs that reference "
                "deleted files. Mark them as `[DEPRECATED]` if the content is "
                "still relevant as historical reference."
            )
        if new_uncovered:
            lines.append(
                "2. **🆕 Add new sources to spec**: "
                "Run `scripts/source-map.py` to refresh the source map, then "
                "consider adding `<!-- REF: ... -->` markers for the new source units "
                "in the relevant spec sections."
            )
        high_moderate = any(
            s.get("impact") in ("high", "moderate")
            for e in affected
            for s in e.get("impacted_sections", [])
        )
        if high_moderate:
            lines.append(
                "3. **🟡 Verify moderate-impact sections**: "
                "Review the affected spec sections above, re-read the changed "
                "source files, and update `<!-- REF: ... -->` line numbers and "
                "section content as needed."
            )
        lines.append(
            "4. **Refresh artifacts**: After updating specs, run:"
        )
        lines.append("   ```bash")
        lines.append("   python {skill_dir}/scripts/build-trace.py --specback-dir .specback --target-dir-for-required final")
        lines.append("   python {skill_dir}/scripts/build-traceability.py --specback-dir .specback --output-dir final")
        lines.append("   ```")
        lines.append("   Then commit the spec updates together with the code changes.")

    lines.append("")

    return "\n".join(lines)


def generate_json(
    result: dict[str, Any],
    base: str,
    changed_files_count: int,
) -> dict[str, Any]:
    """Generate machine-readable JSON drift report."""
    ts = datetime.now(timezone.utc).isoformat()

    # Count unique sections
    section_keys = result.get("section_keys_seen", [])
    deleted_section_keys: set[str] = set()
    for entry in result.get("deleted_with_refs", []):
        for sec in entry.get("impacted_sections", []):
            deleted_section_keys.add(f"{sec['file']}::{sec['section']}")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ts,
        "base": base,
        "summary": {
            "changed_files": changed_files_count,
            "affected_spec_sections": len(section_keys) + len(deleted_section_keys),
            "new_uncovered_sources": len(result.get("new_uncovered", [])),
            "deleted_sources_with_refs": len(result.get("deleted_with_refs", [])),
            "no_impact_changes": len(result.get("no_impact", [])),
        },
        "changes": result.get("affected_sections", []),
        "deleted_with_refs": result.get("deleted_with_refs", []),
        "new_uncovered": result.get("new_uncovered", []),
        "no_impact": result.get("no_impact", []),
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="specback drift detection: identify spec sections affected by code changes",
    )
    parser.add_argument(
        "--specback-dir",
        default=".specback",
        help="Path to .specback/ directory (default: .specback)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for drift reports (default: same as --specback-dir)",
    )
    parser.add_argument(
        "--mode",
        default=None,
        choices=[MODE_AUTO, MODE_GIT, MODE_HASH],
        help="Detection mode: auto (default, detect from artifacts), "
             "git (use git diff), hash (use source-hashes.json)",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Git ref to diff against (default: generated_at_commit from state.json, "
             "fallback HEAD). Only used in git mode.",
    )
    parser.add_argument(
        "--diff",
        default=None,
        help="Raw git diff --name-status text (for CI use; omit to run git diff automatically)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for drift-report.md (default: <output-dir>/drift-report.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also write drift-report.json",
    )
    return parser.parse_args(argv)


def resolve_base(args_base: str | None, specback_path: Path) -> str:
    """Determine the git ref to diff against.

    Priority:
    1. Explicit ``--base`` CLI argument
    2. ``state.json.generated_at_commit`` (Phase 6 recorded this)
    3. ``HEAD`` (fallback)
    """
    if args_base is not None:
        return args_base

    state = load_state(specback_path / "state.json")
    if state is not None:
        commit = state.get("generated_at_commit")
        if commit:
            return str(commit)

    return "HEAD"


def print_base_info(base: str) -> None:
    """Print info about the base ref being used."""
    if base == "HEAD":
        print(
            f"detect-drift.py: using --base HEAD "
            f"(no generated_at_commit in state.json)",
            file=sys.stderr,
        )
    else:
        print(
            f"detect-drift.py: using --base {base[:12]} "
            f"(from state.json.generated_at_commit)",
            file=sys.stderr,
        )


def resolve_mode(
    args_mode: str | None,
    specback_path: Path,
) -> str:
    """Determine the detection mode.

    Priority:
    1. Explicit ``--mode`` CLI argument
    2. ``auto``: if ``.git`` exists and state.json has ``generated_at_commit`` → git
                     elif ``source-hashes.json`` exists → hash
                     else → error
    """
    if args_mode is not None:
        return args_mode

    # AUTO mode: detect from available artifacts
    has_git = (specback_path.parent / ".git").is_dir()
    state = load_state(specback_path / "state.json")
    has_generated_commit = bool(state and state.get("generated_at_commit"))
    has_source_hashes = (specback_path / "source-hashes.json").exists()

    if has_git and has_generated_commit:
        return MODE_GIT
    if has_source_hashes:
        return MODE_HASH
    if has_git:
        # Git repo exists but no commit recorded — still try git
        return MODE_GIT

    print(
        "ERROR: cannot determine detection mode. "
        "Either run from a Git repo with state.json.generated_at_commit, "
        "or run scripts/snapshot-hashes.py first to create source-hashes.json. "
        "Pass --mode git or --mode hash explicitly to override.",
        file=sys.stderr,
    )
    sys.exit(1)


def print_mode_info(mode: str) -> None:
    """Print info about the detection mode being used."""
    mode_labels = {MODE_GIT: "git diff", MODE_HASH: "file hash comparison"}
    label = mode_labels.get(mode, mode)
    print(f"detect-drift.py: using --mode {mode} ({label})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    specback_path = Path(args.specback_dir)
    if not specback_path.is_dir():
        print(
            f"ERROR: {args.specback_dir} is not a directory. "
            "Run from the target project root or pass --specback-dir.",
            file=sys.stderr,
        )
        return 2

    # Resolve output directory
    output_dir = Path(args.output_dir) if args.output_dir is not None else specback_path
    output_dir.mkdir(parents=True, exist_ok=True)

    # -- Resolve detection mode --
    mode = resolve_mode(args.mode, specback_path)
    print_mode_info(mode)

    # -- Load shared artifacts --
    source_map_path = specback_path / "source-map.json"
    trace_path = specback_path / "trace.json"
    source_map = load_source_map(source_map_path)
    trace = load_trace(trace_path)

    # -- Get changes (mode-dependent) --
    changes: list[dict[str, str]] = []
    base = "HEAD"  # default for report header

    if mode == MODE_HASH:
        # Hash comparison mode
        source_hashes = load_source_hashes(
            specback_path / "source-hashes.json",
        )
        target_root = source_hashes.get("target_root", source_map.get("target_root", "."))
        changes = compute_hash_changes(source_hashes, source_map, target_root)
        base = f"hash-snapshot ({source_hashes.get('generated_at', '?')[:19]})"
    elif args.diff is not None:
        # Explicit diff text passed
        changes = parse_diff_text(args.diff)
        base = "stdin"
    else:
        # Git mode
        base = resolve_base(args.base, specback_path)
        print_base_info(base)
        changes = run_git_diff_name_status(base, cwd=str(specback_path.parent))

    if not changes:
        print("detect-drift.py: No changes detected. Spec is up to date.")
        # Write empty report
        empty_md = (
            "# Drift Report\n\n"
            f"<!-- auto-generated by detect-drift.py | "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->\n\n"
            "No changes detected. The spec is up to date.\n"
        )
        output_path = args.output or str(output_dir / "drift-report.md")
        Path(output_path).write_text(empty_md, encoding="utf-8")
        # Contract: when --json is requested, drift-report.json is ALWAYS
        # written (even with zero changes) so gates that consume it do not
        # depend on prior run history (Issue #256).
        if args.json:
            empty_json = generate_json({}, base, 0)
            json_path = output_dir / "drift-report.json"
            json_path.write_text(
                json.dumps(empty_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"detect-drift.py: written to {json_path}")
        return 0

    # -- Analyze --
    result = analyze_impact(changes, source_map, trace)

    # -- Generate reports --
    md = generate_markdown(result, base, len(changes), args.specback_dir)
    output_path = args.output or str(output_dir / "drift-report.md")
    Path(output_path).write_text(md, encoding="utf-8")
    print(f"detect-drift.py: written to {output_path}")

    if args.json:
        json_report = generate_json(result, base, len(changes))
        json_path = output_dir / "drift-report.json"
        json_path.write_text(
            json.dumps(json_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"detect-drift.py: written to {json_path}")

    # Print summary to stderr for logging
    summary = result
    affected_count = len(summary.get("affected_sections", []))
    new_count = len(summary.get("new_uncovered", []))
    deleted_count = len(summary.get("deleted_with_refs", []))
    print(
        f"detect-drift.py: {len(changes)} files changed, "
        f"{affected_count} affected entries, "
        f"{new_count} new uncovered, "
        f"{deleted_count} deleted with refs",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
