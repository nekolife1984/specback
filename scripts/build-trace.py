#!/usr/bin/env python3
"""
specback build-trace.py

Extracts every `<!-- REF: path:start-end -->` written in drafts/*.md (or
final/*.md), matches them against the source units in
`.specback/source-map.json`, and produces `.specback/trace.json`.

This produces in one pass:
- Spec → source citations (the REF the agent wrote, recorded verbatim)
- Source → spec reverse index (`by_source`)
- covered / excluded / uncovered aggregation for MECE verification

Usage:
    python build-trace.py --specback-dir .specback [--output-dir .specback] [--target-dir-for-required final]

Output schema (<output-dir>/trace.json):
    {
      "schema_version": "0.2.0",
      "generated_at": "<ISO>",
      "source_units_total": N,
      "source_units_covered": C,
      "source_units_excluded": E,
      "source_units_uncovered": U,
      "mece_passed": bool,
      "by_source": {
        "SRC-NNNN": {
          "path": "...",
          "line_range": [s, e],
          "covered_by_sections": [{"file": "05-data-model.md", "section": "..."}],
          "excluded": false,
          "excluded_reason": null
        }
      },
      "by_section": {
        "05-data-model.md::5.2 Issue": ["SRC-0142", ...]
      },
      "uncovered_units": ["SRC-NNNN", ...]
    }

Reads `.specback/exclusions.yaml` to honour explicit exclusions. The YAML
file is optional.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from common import utcnow_iso
from pathlib import Path
from typing import Any
from refutils import (
    REF_RE,
    SRC_REF_RE,
    find_refs_in_text,
    index_units_by_path,
    line_ranges_overlap,
    units_for_path,
)

# YAML is optional (not in the stdlib; try/except fallback).
try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


SECTION_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def load_source_map(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"source-map.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_exclusions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if HAS_YAML:
        data = yaml.safe_load(text) or {}
        return list(data.get("exclusions", []))
    # Minimal YAML parse: extract pattern + reason per "- " block.
    items: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                items.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                current[k.strip()] = v.strip().strip("\"'")
        elif ":" in stripped and current is not None:
            k, v = stripped.split(":", 1)
            current[k.strip()] = v.strip().strip("\"'")
    if current:
        items.append(current)
    return items


def is_excluded(unit: dict, exclusions: list[dict]) -> tuple[bool, str | None]:
    for ex in exclusions:
        if "source_id" in ex and ex["source_id"] == unit["id"]:
            return True, ex.get("reason", "")
        if "path" in ex and ex["path"] == unit["path"]:
            return True, ex.get("reason", "")
        if "path_glob" in ex and fnmatch.fnmatch(unit["path"], ex["path_glob"]):
            return True, ex.get("reason", "")
    return False, None


def parse_section_at(lines: list[str], line_no_0idx: int) -> str:
    """Return the nearest `#` heading above the given line as the section name."""
    for i in range(line_no_0idx, -1, -1):
        m = SECTION_RE.match(lines[i])
        if m:
            return m.group(2).strip()
    return "(prelude)"


def scan_drafts_for_refs(drafts_dir: Path, units_by_id: dict[str, dict] | None = None) -> list[dict]:
    """Extract every <!-- REF: ... --> from drafts/*.md (or final/*.md).

    Supports two formats:
    - ``<!-- REF: path:start-end -->`` — direct path + line range reference
    - ``<!-- REF: SRC-NNNN -->`` — indirect source-unit ID (resolved via units_by_id)

    The per-marker parsing is shared with fix-refs.py via
    :func:`refutils.find_refs_in_text` (Issue #281); this function shapes the
    raw scan into the trace schema (draft_file / section columns).
    """
    out: list[dict] = []
    if not drafts_dir.is_dir():
        return out
    for md_file in sorted(drafts_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        for ref in find_refs_in_text(content):
            section = parse_section_at(lines, ref["line_no"])
            if ref["is_src_id"]:
                src_id = ref["ref_path"]
                if units_by_id and src_id in units_by_id:
                    unit = units_by_id[src_id]
                    out.append({
                        "draft_file": md_file.name,
                        "section": section,
                        "ref_path": unit["path"],
                        "ref_start": unit["line_range"][0],
                        "ref_end": unit["line_range"][1],
                    })
                else:
                    # SRC-ID not found in source-map — still record with null range
                    out.append({
                        "draft_file": md_file.name,
                        "section": section,
                        "ref_path": src_id,
                        "ref_start": 0,
                        "ref_end": 0,
                    })
            else:
                out.append({
                    "draft_file": md_file.name,
                    "section": section,
                    "ref_path": ref["ref_path"],
                    "ref_start": ref["ref_start"],
                    "ref_end": ref["ref_end"],
                })
    return out


def resolve_refs_to_units(refs: list[dict], units: list[dict]) -> dict[str, list[dict]]:
    """For each SRC unit ID, return the list of REFs that hit the unit."""
    coverage: dict[str, list[dict]] = {u["id"]: [] for u in units}

    # Index by path for fast lookup (shared helper, Issue #281).
    units_by_path = index_units_by_path(units)

    for ref in refs:
        # Look for an exact or suffix match on the path.
        candidates = units_for_path(ref["ref_path"], units_by_path)

        # Hit units whose line range overlaps the REF range.
        for unit in candidates:
            u_start, u_end = unit["line_range"]
            r_start, r_end = ref["ref_start"], ref["ref_end"]
            if line_ranges_overlap(r_start, r_end, u_start, u_end):
                coverage[unit["id"]].append({
                    "file": ref["draft_file"],
                    "section": ref["section"],
                })
    return coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="specback trace builder")
    parser.add_argument(
        "--specback-dir",
        default=".specback",
        help="Path to .specback/ directory",
    )
    parser.add_argument(
        "--target-dir-for-required",
        default="final",
        help=(
            "Which directory to scan for <!-- REF: ... --> markers: "
            "'drafts'/'final' resolve relative to --specback-dir, or pass an "
            "absolute path to scan any chapter directory (e.g. the output dir)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=".specback",
        help="Path to output trace.json (defaults to --specback-dir value if you only want one dir)",
    )
    args = parser.parse_args(argv)

    sb_dir = Path(args.specback_dir)
    output_dir = Path(args.output_dir)
    source_map_path = sb_dir / "source-map.json"
    target_arg = Path(args.target_dir_for_required)
    drafts_dir = target_arg if target_arg.is_absolute() else sb_dir / target_arg
    output_path = output_dir / "trace.json"
    exclusions_path = sb_dir / "exclusions.yaml"

    output_dir.mkdir(parents=True, exist_ok=True)

    if not source_map_path.exists():
        print(
            f"ERROR: {source_map_path} not found. Run scripts/source-map.py first.",
            file=sys.stderr,
        )
        return 2

    sm = load_source_map(source_map_path)
    units = sm.get("units", [])

    # Build an index for SRC-ID resolution
    units_by_id: dict[str, dict] = {u["id"]: u for u in units if "id" in u}

    exclusions = load_exclusions(exclusions_path)

    refs = scan_drafts_for_refs(drafts_dir, units_by_id)
    coverage = resolve_refs_to_units(refs, units)

    by_source: dict[str, dict[str, Any]] = {}
    by_section: dict[str, list[str]] = {}
    uncovered: list[str] = []
    covered_count = 0
    excluded_count = 0

    for u in units:
        excluded, reason = is_excluded(u, exclusions)
        sections = coverage.get(u["id"], [])
        # Collapse duplicates within the same chapter section.
        uniq_sections: list[dict[str, str]] = []
        seen = set()
        for s in sections:
            key = (s["file"], s["section"])
            if key not in seen:
                seen.add(key)
                uniq_sections.append(s)

        by_source[u["id"]] = {
            "path": u["path"],
            "line_range": u["line_range"],
            "kind": u["kind"],
            "name": u["name"],
            "covered_by_sections": uniq_sections,
            "excluded": excluded,
            "excluded_reason": reason,
        }
        if uniq_sections:
            covered_count += 1
            for s in uniq_sections:
                section_key = f"{s['file']}::{s['section']}"
                by_section.setdefault(section_key, []).append(u["id"])
        elif excluded:
            excluded_count += 1
        else:
            uncovered.append(u["id"])

    total = len(units)
    uncovered_count = len(uncovered)
    mece_passed = uncovered_count == 0

    trace = {
        "schema_version": "0.2.0",
        "generated_at": utcnow_iso(),
        "source_units_total": total,
        "source_units_covered": covered_count,
        "source_units_excluded": excluded_count,
        "source_units_uncovered": uncovered_count,
        "mece_passed": mece_passed,
        "by_source": by_source,
        "by_section": by_section,
        "uncovered_units": uncovered,
    }
    output_path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"build-trace.py: total={total} covered={covered_count} "
        f"excluded={excluded_count} uncovered={uncovered_count} "
        f"mece_passed={mece_passed}"
    )
    if uncovered:
        print(f"  uncovered SRC sample: {uncovered[:5]}", file=sys.stderr)
    return 0 if mece_passed else 1


if __name__ == "__main__":
    sys.exit(main())
