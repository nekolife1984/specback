#!/usr/bin/env python3
"""Restore a source-map.json to old IDs + append new units (after full regeneration).

Background: a full re-run of source-map.py renumbers SRC-IDs in file-scan
order, silently breaking every existing `<!-- REF: SRC-NNNN -->` marker in
the spec (build-trace.py does not error on a valid-looking ID that now
points at a different unit). When you only ADD a new .py module, restore the
old units from the committed `specs/trace.json` (git-tracked, its `by_source`
holds the previous mapping) and append the new module's units at the end with
fresh IDs.

Usage:
    python3 restore-sourcemap-from-trace.py \\
        --repo /path/to/codebase \\
        [--new-ids SRC-0014,SRC-0015,...]   # IDs assigned to the new module in the re-generated map

Notes:
    - New unit metadata (path/line_range/kind/name) is taken from the fully
      re-generated source-map.json; this script renumbers them from
      old_max_id + 1 and appends at the end.
    - After running, ALWAYS regenerate inventory.json from the restored
      source-map (otherwise coverage-check FAILs on INV-NNNN.related_source_ids
      referencing IDs not in source-map.json).
    - REFs in newly written spec sections must also be updated to the new
      appended IDs (e.g. sed-replace SRC-0014 -> SRC-0034 in the new section).

Real case: ai-chat Issue #29 / F-008 added wiki_ui.py (14 functions).
Old 33 units + 14 new = 47 units, uncovered=0 / mece_passed=True.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="Codebase root (has specs/)")
    p.add_argument(
        "--new-ids",
        default="",
        help="Comma-separated IDs assigned to the new module in the re-generated map "
        "(e.g. SRC-0014,SRC-0015)",
    )
    args = p.parse_args()

    repo = Path(args.repo)
    old_trace = repo / "specs/trace.json"  # committed; source of truth for old units
    new_map = (
        repo / "specs/.specback/source-map.json"
    )  # re-generated map (to be replaced)
    out = repo / "specs/.specback/source-map.json"

    old = json.loads(old_trace.read_text(encoding="utf-8"))
    new = json.loads(new_map.read_text(encoding="utf-8"))

    # 1) Restore old units from by_source (preserve original order)
    old_units: list[dict] = []
    for uid, info in old["by_source"].items():
        old_units.append(
            {
                "id": uid,
                "path": info["path"],
                "line_range": info["line_range"],
                "kind": info["kind"],
                "name": info["name"],
            }
        )

    # 2) Extract new units and renumber from old_max_id + 1, appending at the end
    new_ids = {s.strip() for s in args.new_ids.split(",") if s.strip()}
    max_old = max(int(u["id"].split("-")[1]) for u in old_units)
    added: list[dict] = []
    for u in new["units"]:
        if u["id"] in new_ids:
            added.append(
                {
                    "id": f"SRC-{max_old + 1 + len(added):04d}",
                    "path": u["path"],
                    "line_range": u["line_range"],
                    "kind": u["kind"],
                    "name": u["name"],
                }
            )

    merged = old_units + added
    result = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now().isoformat(),
        "units": merged,
    }
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print(
        f"Restored: {len(old_units)} old units + {len(added)} new = {len(merged)} units"
    )
    print("New unit IDs:", [u["id"] for u in added])


if __name__ == "__main__":
    main()
