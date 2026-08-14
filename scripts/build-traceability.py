#!/usr/bin/env python3
"""
specback build-traceability.py

Reads `.specback/trace.json` and auto-generates a human-readable
`traceability.md` under the specified output directory.

Produces:
- Chapter → Source mapping table
- Source → Chapter mapping table
- MECE check result (coverage rate, exclusion breakdown)

so the user can see everything in a single Markdown file. Never write it
by hand.

Usage:
    python build-traceability.py --specback-dir .specback --output-dir . --stage final
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from common import utcnow_iso
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="specback traceability.md generator")
    parser.add_argument("--specback-dir", default=".specback")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: same as --specback-dir)")
    parser.add_argument(
        "--stage",
        choices=["drafts", "final"],
        default="final",
        help="Write to a stage subdirectory under --output-dir",
    )
    args = parser.parse_args(argv)

    sb_dir = Path(args.specback_dir)
    trace_path = sb_dir / "trace.json"
    if not trace_path.exists():
        print(
            f"ERROR: {trace_path} not found. Run scripts/build-trace.py first.",
            file=sys.stderr,
        )
        return 2

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    output_dir = args.output_dir or Path(args.specback_dir)
    output_path = output_dir / args.stage / "traceability.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = trace["source_units_total"]
    covered = trace["source_units_covered"]
    excluded = trace["source_units_excluded"]
    uncovered = trace["source_units_uncovered"]
    mece_ok = trace["mece_passed"]
    coverage_rate = (covered / total * 100) if total else 0.0

    lines: list[str] = []
    lines.append("# Traceability")
    lines.append("")
    lines.append(
        f"<!-- auto-generated: {utcnow_iso()} | source: trace.json -->"
    )
    lines.append("")
    lines.append("## MECE check result")
    lines.append("")
    lines.append(f"- Total extracted source units: **{total}**")
    lines.append(f"- Covered by the spec: **{covered} ({coverage_rate:.1f}%)**")
    lines.append(f"- Explicitly excluded: **{excluded}**")
    status_mark = "✅ PASSED" if mece_ok else "❌ FAILED"
    lines.append(f"- Uncovered: **{uncovered}** {status_mark}")
    lines.append("")

    if uncovered:
        lines.append("### Uncovered list (action required)")
        lines.append("")
        lines.append("| SRC ID | path | line range | kind | name |")
        lines.append("|--------|------|--------|------|------|")
        for sid in trace.get("uncovered_units", []):
            u = trace["by_source"].get(sid, {})
            lr = u.get("line_range", [0, 0])
            lines.append(
                f"| {sid} | `{u.get('path','')}` | {lr[0]}-{lr[1]} | "
                f"{u.get('kind','')} | {u.get('name','')} |"
            )
        lines.append("")

    # Exclusion breakdown
    excluded_units = [
        (sid, u) for sid, u in trace["by_source"].items() if u.get("excluded")
    ]
    if excluded_units:
        lines.append("### Explicit-exclusion breakdown")
        lines.append("")
        lines.append("| SRC ID | path | exclusion reason |")
        lines.append("|--------|------|---------|")
        for sid, u in excluded_units:
            reason = u.get("excluded_reason") or "(no reason given)"
            lines.append(f"| {sid} | `{u.get('path','')}` | {reason} |")
        lines.append("")

    # Chapter → Source mapping table
    lines.append("## Chapter → Source mapping")
    lines.append("")
    by_section = trace.get("by_section", {})
    if by_section:
        # Group by file name and section name
        sections_by_file: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
        for key, src_ids in by_section.items():
            if "::" in key:
                file_name, section = key.split("::", 1)
            else:
                file_name, section = key, "(no section)"
            sections_by_file[file_name].append((section, src_ids))

        for file_name in sorted(sections_by_file):
            lines.append(f"### {file_name}")
            lines.append("")
            for section, src_ids in sections_by_file[file_name]:
                src_descs = []
                for sid in src_ids[:50]:  # up to 50 per chapter
                    u = trace["by_source"].get(sid, {})
                    lr = u.get("line_range", [0, 0])
                    p = u.get("path", "")
                    src_descs.append(f"{sid} (`{Path(p).name}:{lr[0]}-{lr[1]}`)")
                more = f" ... and {len(src_ids)-50} more" if len(src_ids) > 50 else ""
                lines.append(f"- **{section}** — {', '.join(src_descs)}{more}")
            lines.append("")
    else:
        lines.append("_(no citations recorded)_")
        lines.append("")

    # Source → Chapter mapping table
    lines.append("## Source → Chapter mapping (by file)")
    lines.append("")
    by_source = trace.get("by_source", {})
    grouped_by_path: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for sid, u in by_source.items():
        grouped_by_path[u.get("path", "")].append((sid, u))

    for path in sorted(grouped_by_path):
        sids = grouped_by_path[path]
        # Skip display when all SRC have empty covered_by_sections and are excluded
        all_empty = all(
            not u.get("covered_by_sections") and not u.get("excluded")
            for _, u in sids
        )
        if all_empty and len(sids) > 0:
            continue
        lines.append(f"### `{path}`")
        lines.append("")
        for sid, u in sorted(sids, key=lambda x: x[1].get("line_range", [0, 0])[0]):
            lr = u.get("line_range", [0, 0])
            name = u.get("name", "")
            kind = u.get("kind", "")
            covered_by = u.get("covered_by_sections", [])
            if covered_by:
                where = ", ".join(
                    f"{c.get('file','')} §{c.get('section','')}" for c in covered_by[:5]
                )
                more = f" (and {len(covered_by)-5} more)" if len(covered_by) > 5 else ""
                lines.append(
                    f"- **{sid}** ({kind} `{name}` lines {lr[0]}-{lr[1]}) → {where}{more}"
                )
            elif u.get("excluded"):
                lines.append(
                    f"- ~~{sid}~~ ({kind} `{name}` lines {lr[0]}-{lr[1]}) "
                    f"— **excluded**: {u.get('excluded_reason','')}"
                )
            else:
                lines.append(
                    f"- ⚠️ **{sid}** ({kind} `{name}` lines {lr[0]}-{lr[1]}) — **UNCOVERED**"
                )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"build-traceability.py: written to {output_path} "
        f"(mece_passed={mece_ok}, coverage={coverage_rate:.1f}%)"
    )
    return 0 if mece_ok else 1


if __name__ == "__main__":
    sys.exit(main())
