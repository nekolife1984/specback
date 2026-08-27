#!/usr/bin/env python3
"""
snapshot-hashes.py — record SHA256 hashes of source-map units for drift detection.

Reads ``source-map.json`` and computes a SHA256 hash of the content of each
SRC-ID's line range. The output is consumed by ``detect-drift.py --mode hash``
to detect code changes without requiring Git.

Usage
-----
    python snapshot-hashes.py --specback-dir {output_dir}/.specback
    python snapshot-hashes.py --specback-dir {output_dir}/.specback --output {output_dir}/.specback/source-hashes.json

Output
------
    {output_dir}/.specback/source-hashes.json

Dependencies
------------
    Python 3.10+ (stdlib only).
"""

from __future__ import annotations

import argparse
import os
import sys
from common import (
    add_specback_dir_arg,
    atomic_write_json,
    hash_line_range,
    load_json_text,
    resolve_target_root,
    utcnow_iso,
)
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "0.1.0"
HASH_ALGORITHM = "sha256"
LINE_HASH_BYTES = 4096  # read up to 4KB per line for hash stability


def compute_hashes(
    units: list[dict[str, Any]],
    target_root: str | Path,
) -> dict[str, dict[str, Any]]:
    """Compute hash for each SRC-ID unit.

    Returns dict keyed by SRC-ID with hash, line_count, etc.
    """
    result: dict[str, dict[str, Any]] = {}
    for unit in units:
        uid = unit.get("id", "")
        file_path_rel = unit.get("path", "")
        line_range = unit.get("line_range", [0, 0])
        if not uid or not file_path_rel:
            continue

        abs_path = Path(target_root) / file_path_rel
        line_start, line_end = line_range

        try:
            digest, line_count = hash_line_range(abs_path, line_start, line_end)
        except FileNotFoundError:
            print(
                f"WARNING: file not found for {uid} ({file_path_rel}), "
                f"marking as MISSING",
                file=sys.stderr,
            )
            result[uid] = {
                "id": uid,
                "path": file_path_rel,
                "line_range": line_range,
                "line_count": 0,
                "hash": "",
                "status": "MISSING",
            }
            continue
        except OSError as e:
            print(
                f"WARNING: cannot read {file_path_rel}: {e}, "
                f"marking as MISSING",
                file=sys.stderr,
            )
            result[uid] = {
                "id": uid,
                "path": file_path_rel,
                "line_range": line_range,
                "line_count": 0,
                "hash": "",
                "status": "MISSING",
            }
            continue

        result[uid] = {
            "id": uid,
            "path": file_path_rel,
            "line_range": line_range,
            "line_count": line_count,
            "hash": f"{HASH_ALGORITHM}:{digest}",
            "status": "OK",
        }

    return result


def detect_scan_patterns(target_root: str | Path) -> dict[str, list[str]]:
    """Detect common source file patterns from the target root.

    Returns scan/include patterns based on observed file extensions.
    This is a best-effort heuristic for detecting new files.
    """
    source_extensions = {
        ".py", ".rb", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".kts",
        ".go", ".rs", ".php", ".cs", ".swift", ".c", ".cpp", ".h", ".hpp",
        ".sql", ".r", ".scala", ".ex", ".exs", ".vue", ".svelte",
    }
    exclude_dirs = {
        ".git", ".svn", "node_modules", "vendor", ".venv", "venv",
        "__pycache__", ".specbridge", ".specback", ".spectra", "dist", "build",
        ".hermes", ".claude", ".cursor",
    }

    observed: set[str] = set()
    try:
        for entry in os.scandir(target_root):
            if entry.name.startswith(".") or entry.name in exclude_dirs:
                continue
            if entry.is_dir():
                # Shallow scan first level
                try:
                    for sub in os.scandir(entry.path):
                        ext = os.path.splitext(sub.name)[1].lower()
                        if ext in source_extensions:
                            observed.add(f"**/*{ext}")
                except PermissionError:
                    continue
            else:
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in source_extensions:
                    observed.add(f"**/*{ext}")
    except PermissionError:
        pass

    patterns = sorted(observed) if observed else ["**/*.py", "**/*.rb", "**/*.js", "**/*.ts"]
    return {
        "include_patterns": patterns,
        "exclude_patterns": [f"**/{d}/**" for d in sorted(exclude_dirs)],
    }


def build_output(
    units_hashes: dict[str, dict[str, Any]],
    target_root: str,
    resolved_target_root: str,
    source_map_ref: str,
    scan_info: dict[str, list[str]],
) -> dict[str, Any]:
    """Assemble the final source-hashes.json document.

    ``target_root`` keeps the *portable* recorded root and
    ``resolved_target_root`` records the absolute root used for this run — so a
    moved repo can be re-hashed without ambiguity (Issue #380 / SB-09).
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow_iso(),
        "target_root": target_root,
        "resolved_target_root": resolved_target_root,
        "source_map_ref": source_map_ref,
        "units_total": len(units_hashes),
        "units": units_hashes,
        "scan_patterns": scan_info,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="specback: snapshot SHA256 hashes of source-map units for drift detection",
    )
    add_specback_dir_arg(parser)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for source-hashes.json "
             "(default: same as --specback-dir)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for source-hashes.json "
             "(default: <output-dir>/source-hashes.json)",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Absolute path to the project root used to re-resolve the portable "
             "target_root. When omitted, the specback dir's parent is used "
             "(Issue #380 / SB-09: moved-repo re-resolution).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    specback_path = Path(args.specback_dir)
    if not specback_path.is_dir():
        print(
            f"ERROR: {args.specback_dir} is not a directory.",
            file=sys.stderr,
        )
        return 2

    source_map_path = specback_path / "source-map.json"
    if not source_map_path.exists():
        print(
            f"ERROR: {source_map_path} not found. Run scripts/source-map.py first.",
            file=sys.stderr,
        )
        return 2

    # Load source-map
    sm = load_json_text(source_map_path)
    units = sm.get("units", [])
    if not units:
        print(
            "WARNING: source-map.json has no units. Nothing to hash — writing an empty "
            "source-hashes.json.",
            file=sys.stderr,
        )

    target_root = sm.get("target_root", ".")

    # Re-resolve the portable target_root against the current project root
    # (specback dir parent, or --project-root) so a moved repo still hashes
    # every unit instead of marking them all MISSING (Issue #380 / SB-09).
    resolved_root = resolve_target_root(specback_path, target_root,
                                        project_root=args.project_root)

    # Compute hashes
    units_hashes = compute_hashes(units, resolved_root)

    # Detect scan patterns for future new-file detection
    scan_info = detect_scan_patterns(resolved_root)

    # Build output
    source_map_ref = (
        f"source-map.json ({sm.get('schema_version', '?')})"
        f" — {len(units)} units"
    )
    output = build_output(units_hashes, str(target_root),
                          str(resolved_root), source_map_ref, scan_info)

    output_dir = Path(args.output_dir) if args.output_dir else specback_path
    output_path = args.output or str(output_dir / "source-hashes.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, output)

    ok_count = sum(
        1 for u in units_hashes.values() if u.get("status") == "OK"
    )
    missing_count = sum(
        1 for u in units_hashes.values() if u.get("status") == "MISSING"
    )

    print(
        f"snapshot-hashes.py: {len(units)} units processed, "
        f"{ok_count} hashed, {missing_count} missing, "
        f"written to {output_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
