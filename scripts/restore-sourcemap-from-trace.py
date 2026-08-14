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
        [--apply]                           # write the restored map (default: dry-run)

Notes:
    - New unit metadata (path/line_range/kind/name) is taken from the fully
      re-generated source-map.json; this script renumbers them from
      old_max_id + 1 and appends at the end.
    - Dry-run by default; pass --apply to write. Before writing, a backup is
      saved to source-map.json.pre-restore and the file is replaced
      atomically (temp file + os.replace).
    - The restored map carries `restored_from: "trace.json"`; re-running the
      script on an already-restored map is refused (--force to override)
      because it would silently swap the appended units' identities.
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
import os
import re
import shutil
import subprocess
import sys
from common import utcnow_iso
from pathlib import Path

SRC_ID_RE = re.compile(r"^SRC-(\d+)$")

RESTORED_MARKER = "trace.json"


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _load_json_object(path: Path, what: str) -> dict:
    """Load a JSON object, exiting with a clean error on any failure."""
    if not path.exists():
        _fail(f"{what} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        _fail(f"cannot read {what} {path}: {exc}")
    if not isinstance(data, dict):
        _fail(f"{what} must be a JSON object: {path}")
    return data


def _warn_if_dirty_trace(repo: Path, trace_rel: str) -> None:
    """Warn when specs/trace.json has uncommitted changes (best-effort)."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", trace_rel],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return  # not a git repo or git unavailable — skip the check
    if proc.returncode != 0:
        print(
            "WARNING: specs/trace.json has uncommitted changes; restoring from a "
            "dirty trace may reintroduce stale units. Pass --force to proceed.",
            file=sys.stderr,
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="Codebase root (has specs/)")
    p.add_argument(
        "--new-ids",
        default="",
        help="Comma-separated IDs assigned to the new module in the re-generated map "
        "(e.g. SRC-0014,SRC-0015)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the restored map (default: dry-run, report only)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the map looks already restored or trace.json is dirty",
    )
    args = p.parse_args()

    repo = Path(args.repo)
    trace_path = repo / "specs/trace.json"
    new_map_path = repo / "specs/.specback/source-map.json"

    old = _load_json_object(trace_path, "trace.json")
    new = _load_json_object(new_map_path, "source-map.json")

    # 0) Idempotency guard: refuse to re-run on an already-restored map.
    if new.get("restored_from") == RESTORED_MARKER and not args.force:
        _fail(
            "source-map.json was already restored by this script. Re-running would "
            "renumber or swap the appended units' identities. Pass --force only if "
            "the re-generated map is fresh."
        )

    # 1) Restore old units from by_source (preserve original order).
    by_source = old.get("by_source")
    if not isinstance(by_source, dict) or not by_source:
        _fail(f"trace.json has no by_source entries: {trace_path}")
    assert isinstance(by_source, dict)  # mypy narrowing
    old_units: list[dict] = []
    for uid, info in by_source.items():
        if not isinstance(info, dict) or not isinstance(info.get("path"), str):
            _fail(f"trace.json by_source entry is malformed: {uid}")
        old_units.append(
            {
                "id": uid,
                "path": info["path"],
                "line_range": info.get("line_range", [0, 0]),
                "kind": info.get("kind", ""),
                "name": info.get("name", ""),
                "fingerprint": info.get("fingerprint", ""),
                "signature": info.get("signature", ""),
            }
        )
    missing_fp = [u["id"] for u in old_units if not u.get("fingerprint")]
    if missing_fp:
        print(
            "WARNING: old units restored without fingerprint (drift detection may "
            f"be degraded): {', '.join(missing_fp[:5])}"
            + ("..." if len(missing_fp) > 5 else ""),
            file=sys.stderr,
        )

    # 2) Validate --new-ids before doing anything destructive.
    old_ids = {u["id"] for u in old_units}
    new_ids = {s.strip() for s in args.new_ids.split(",") if s.strip()}
    new_unit_ids = {
        u.get("id")
        for u in new.get("units", [])
        if isinstance(u, dict) and u.get("id") is not None
    }
    malformed = [i for i in sorted(new_ids) if not SRC_ID_RE.match(i)]
    if malformed:
        _fail(f"--new-ids contains a malformed ID: {malformed} (expected SRC-NNNN)")
    bad = sorted(new_ids - new_unit_ids)
    if bad:
        _fail(f"--new-ids contains IDs not present in the re-generated map: {bad}")
    collide = sorted(new_ids & old_ids)
    if collide:
        _fail(f"--new-ids contains old unit IDs (would duplicate units): {collide}")

    # 3) Extract new units and renumber from old_max_id + 1, appending at the end.
    max_old = max(
        (int(m.group(1)) for u in old_units if (m := SRC_ID_RE.match(u["id"]))),
        default=0,
    )
    added: list[dict] = []
    for u in new.get("units", []):
        if not isinstance(u, dict) or u.get("id") not in new_ids:
            continue
        added.append(
            {
                "id": f"SRC-{max_old + 1 + len(added):04d}",
                "path": u.get("path", ""),
                "line_range": u.get("line_range", [0, 0]),
                "kind": u.get("kind", ""),
                "name": u.get("name", ""),
                "fingerprint": u.get("fingerprint", ""),
                "signature": u.get("signature", ""),
            }
        )
    if len(added) != len(new_ids):
        _fail(
            f"could not resolve all --new-ids ({len(new_ids)} requested, "
            f"{len(added)} found)"
        )

    merged = old_units + added
    result = {
        "schema_version": "0.1.0",
        "generated_at": utcnow_iso(),
        "restored_from": RESTORED_MARKER,
        "units": merged,
    }

    if not args.apply:
        print("Dry-run (use --apply to write):")
        print(
            f"  Restored: {len(old_units)} old units + {len(added)} new = "
            f"{len(merged)} units"
        )
        print("  New unit IDs:", [u["id"] for u in added])
        return

    _warn_if_dirty_trace(repo, "specs/trace.json")

    # 4) Backup + atomic write (never truncate the only copy in place).
    backup_path = new_map_path.with_name("source-map.json.pre-restore")
    if new_map_path.exists() and not backup_path.exists():
        shutil.copy2(new_map_path, backup_path)
        print(f"backup: {backup_path}", file=sys.stderr)
    tmp_path = new_map_path.with_name("source-map.json.tmp")
    tmp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, new_map_path)

    print(
        f"Restored: {len(old_units)} old units + {len(added)} new = "
        f"{len(merged)} units"
    )
    print("New unit IDs:", [u["id"] for u in added])


if __name__ == "__main__":
    main()
