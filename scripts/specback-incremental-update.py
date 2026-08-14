#!/usr/bin/env python3
"""
specback-incremental-update.py — Incremental spec update (Issue #269).

Turns a Phase 7 drift report (``drift-report.json``) into a chapter-scoped
spec update loop:

    plan    — identify affected chapters, generate per-chapter re-investigation
              prompts, snapshot chapter hashes (zero-collateral baseline).
    verify  — check an updated chapter: target membership, SRC-ID existence
              (renumbering-trap guard), zero collateral edits.
    apply   — same checks as verify, then backup + atomic replace + trace refresh.

This is the "partial update" loop for maintenance-mode specs: only the chapters
affected by the drift are re-investigated and replaced; every other chapter must
remain byte-identical (verified against the ``plan`` baseline).

Usage
-----
    python specback-incremental-update.py plan \\
        --specback-dir .specback --output-dir specs [--drift-report drift-report.json]
    python specback-incremental-update.py verify \\
        --specback-dir .specback --output-dir specs --updated <chapter.md>
    python specback-incremental-update.py apply \\
        --specback-dir .specback --output-dir specs --updated <chapter.md>

Common flags
------------
    --specback-dir PATH   Directory holding wbs.json / source-map.json / trace.json
                          (default: .specback)
    --output-dir PATH     Where final spec chapters live (default: .)
    --drift-report PATH   Drift report input (default: {specback-dir}/drift-report.json)
    --json                Machine-readable stdout for plan/verify/apply

apply-only flag
---------------
    --skip-trace-refresh  Do not run build-trace.py after replacing the chapter

Exit codes
----------
    0 — ok (plan: prompts written; verify: passed; apply: applied)
    1 — error: missing/unreadable input, failed checks, or build-trace failure
    2 — plan: drift report contains no affected chapters (state.json still written)
    3 — verify/apply: state.json missing (run plan first)

Security posture (matches #253/#267/#273 hardening):
    - Chapter names from drift-report.json are validated (basename + chapter
      pattern) before any path join — no path traversal writes.
    - All temp/prompt/backup writes refuse to follow symlinks (O_NOFOLLOW).
    - JSON inputs reject non-dict roots, non-list container fields, and
      non-finite constants (NaN/Infinity); capped at 50 MiB.
    - Atomic replace via temp file + os.replace; backups before destructive
      replace; apply re-verifies the exact bytes it writes (TOCTOU-safe).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from common import sha256_file, utcnow_iso
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"

MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB guard against unbounded reads

CHAPTER_RE = re.compile(r"^(0\d|[1-9]\d)-[a-z0-9-]+\.md$")
RESERVED_FILES = {"00-metadata.md", "99-unresolved.md", "traceability.md"}
SRC_ID_RE = re.compile(r"<!--\s*REF:\s*(SRC-\d+)\s*-->")
ALL_REF_RE = re.compile(r"<!--\s*REF:\s*([^>]*?)\s*-->")

INCREMENTAL_DIR_NAME = "incremental"
PROMPTS_DIR_NAME = "prompts"
UPDATED_DIR_NAME = "updated"
STATE_FILE_NAME = "state.json"


def _fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def _load_json_object(path: Path, what: str) -> dict:
    """Load a JSON object, guarding against size, malformed JSON, and non-dicts."""
    if not path.exists():
        _fail(f"{what} not found: {path}")
    try:
        with open(path, "rb") as fh:
            # Size-check the opened fd (TOCTOU-safe: same bytes we read).
            if os.fstat(fh.fileno()).st_size > MAX_INPUT_BYTES:
                _fail(f"{what} exceeds {MAX_INPUT_BYTES} bytes: {path}")
            data = json.loads(fh.read().decode("utf-8"),
                              parse_constant=_reject_nonfinite)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
        _fail(f"cannot read {what}: {path}: {exc}")
    if not isinstance(data, dict):
        _fail(f"{what} is not a JSON object: {path}")
    return data


def _as_list(value: Any, what: str) -> list:
    """Return *value* if it is a list, else fail cleanly (no raw tracebacks)."""
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(f"{what} must be a list, got {type(value).__name__}")
    return value


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _is_chapter_file(name: str) -> bool:
    return name in RESERVED_FILES or bool(CHAPTER_RE.match(name))


def _valid_chapter_name(name: Any) -> bool:
    """Chapter names must be basenames matching the chapter naming pattern."""
    if not isinstance(name, str):
        return False
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return False
    return _is_chapter_file(name)


def _md_escape(s: Any) -> str:
    """Escape a drift/wbs value before interpolating into a markdown prompt."""
    text = str(s)
    return (text.replace("\\", "\\\\")
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("`", "\\`"))


def _write_new_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Create/overwrite *path* without following symlinks (O_NOFOLLOW).

    The existing entry (if any) is unlinked first — matching os.replace
    semantics — then the file is created with O_EXCL so a racing symlink
    cannot redirect the write. Used for all temp/prompt/state outputs.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def _collect_affected_chapters(drift: dict) -> list[str]:
    """Collect unique, validated affected chapter file names from the drift report."""
    files: set[str] = set()
    for entry in _as_list(drift.get("changes"), "drift-report.changes") + \
            _as_list(drift.get("deleted_with_refs"), "drift-report.deleted_with_refs"):
        if not isinstance(entry, dict):
            continue
        for sec in _as_list(entry.get("impacted_sections"),
                            "drift-report impacted_sections"):
            if isinstance(sec, dict) and _valid_chapter_name(sec.get("file")):
                files.add(sec["file"])
    return sorted(files)


def _chapter_title(wbs: dict, chapter_file: str) -> str:
    for ch in _as_list(wbs.get("chapters"), "wbs.chapters"):
        if isinstance(ch, dict) and ch.get("filename") == chapter_file:
            return str(ch.get("title", ""))
    return ""


def _collect_chapter_src_ids(drift: dict, trace: dict, chapter_file: str) -> list[str]:
    """SRC-IDs whose covered sections live in this chapter file (union of both sources)."""
    ids: set[str] = set()
    for entry in _as_list(drift.get("changes"), "drift-report.changes") + \
            _as_list(drift.get("deleted_with_refs"), "drift-report.deleted_with_refs"):
        if not isinstance(entry, dict):
            continue
        for sec in _as_list(entry.get("impacted_sections"),
                            "drift-report impacted_sections"):
            if isinstance(sec, dict) and sec.get("file") == chapter_file:
                for sid in _as_list(entry.get("src_ids"), "entry.src_ids"):
                    if isinstance(sid, str):
                        ids.add(sid)
    # Always union with the trace.json reverse index (covers path-only entries).
    by_source = trace.get("by_source", {})
    if isinstance(by_source, dict):
        for sid, info in by_source.items():
            if not isinstance(info, dict):
                continue
            for sec in _as_list(info.get("covered_by_sections"),
                                "trace covered_by_sections"):
                if isinstance(sec, dict) and sec.get("file") == chapter_file:
                    ids.add(str(sid))
    return sorted(ids)


def _collect_changed_sources(drift: dict, chapter_src_ids: set[str]) -> list[str]:
    """Changed source files whose SRC-IDs touch the chapter."""
    sources: set[str] = set()
    for entry in _as_list(drift.get("changes"), "drift-report.changes") + \
            _as_list(drift.get("deleted_with_refs"), "drift-report.deleted_with_refs"):
        if not isinstance(entry, dict):
            continue
        entry_ids = {s for s in _as_list(entry.get("src_ids"), "entry.src_ids")
                     if isinstance(s, str)}
        if entry_ids & chapter_src_ids and isinstance(entry.get("file"), str):
            sources.add(entry["file"])
    return sorted(sources)


def _render_prompt(
    chapter_file: str,
    title: str,
    drift: dict,
    changed_sources: list[str],
    src_ids: list[str],
    specback_dir: Path,
) -> str:
    generated_at = drift.get("generated_at", "?")
    base = drift.get("base", "?")
    lines = [
        f"# Incremental re-investigation: {chapter_file}",
        "",
        "## Context",
        f"- Drift detected at {_md_escape(generated_at)} against base "
        f"{_md_escape(base)} (from drift-report.json).",
        f"- This chapter ({_md_escape(title)}) is affected by source changes; "
        "re-read the changed sources and update ONLY this chapter. Do not touch "
        "other chapters (zero collateral edits required).",
        "",
        "## Changed sources affecting this chapter",
    ]
    if changed_sources:
        for src in changed_sources:
            lines.append(f"- {_md_escape(src)}")
    else:
        lines.append("- (none listed in drift report; re-check the SRC-IDs below)")
    lines += ["", "## SRC-IDs to re-check"]
    for sid in src_ids:
        lines.append(f"- {_md_escape(sid)}")
    if not src_ids:
        lines.append("- (none collected)")
    lines += [
        "",
        "## Instructions",
        "1. Read each changed source file listed above.",
        "2. Update the chapter body where statements are now stale; keep statements "
        "that are still correct.",
        "3. Keep every `<!-- REF: ... -->` citation valid: REFs must point at SRC-IDs "
        "that exist in source-map.json (never invent or renumber SRC-IDs).",
        "4. Keep the existing `<!-- CONFIDENCE: -->`, `<!-- ASSUMED: -->`, "
        "`<!-- ASK SME -->` marker conventions.",
        f"5. Output the full updated chapter markdown and save it as "
        f"{_md_escape(specback_dir / 'incremental' / 'updated' / chapter_file)}.",
        "",
    ]
    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace) -> int:
    specback_dir = Path(args.specback_dir)
    output_dir = Path(args.output_dir)
    drift_path = Path(args.drift_report)
    wbs_path = specback_dir / "wbs.json"
    trace_path = specback_dir / "trace.json"
    source_map_path = specback_dir / "source-map.json"

    drift = _load_json_object(drift_path, "drift-report")
    wbs = _load_json_object(wbs_path, "wbs.json")
    trace = _load_json_object(trace_path, "trace.json")
    source_map = _load_json_object(source_map_path, "source-map.json")

    affected = _collect_affected_chapters(drift)
    # Hard-fail on any malformed chapter name instead of silently dropping it.
    for entry in _as_list(drift.get("changes"), "drift-report.changes") + \
            _as_list(drift.get("deleted_with_refs"), "drift-report.deleted_with_refs"):
        if not isinstance(entry, dict):
            continue
        for sec in _as_list(entry.get("impacted_sections"),
                            "drift-report impacted_sections"):
            if isinstance(sec, dict) and isinstance(sec.get("file"), str) \
                    and not _valid_chapter_name(sec["file"]):
                _fail(f"drift report contains invalid chapter filename: "
                      f"{sec['file']!r}")

    incremental_dir = specback_dir / INCREMENTAL_DIR_NAME
    prompts_dir = incremental_dir / PROMPTS_DIR_NAME
    updated_dir = incremental_dir / UPDATED_DIR_NAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    updated_dir.mkdir(parents=True, exist_ok=True)

    existing_src_ids = {
        u.get("id") for u in _as_list(source_map.get("units"), "source-map.units")
        if isinstance(u, dict)
    }

    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow_iso(),
        "affected_chapters": [],
        "chapter_hashes": {},
    }
    chapters_out: list[dict[str, Any]] = []

    for chapter_file in affected:
        title = _chapter_title(wbs, chapter_file)
        src_ids = _collect_chapter_src_ids(drift, trace, chapter_file)
        changed_sources = _collect_changed_sources(drift, set(src_ids))
        prompt = _render_prompt(
            chapter_file, title, drift, changed_sources, src_ids, specback_dir
        )
        prompt_path = prompts_dir / chapter_file
        _write_new_file(prompt_path, prompt.encode("utf-8"))

    # Snapshot hashes for ALL chapter files in the output dir — not just the
    # affected ones, and including wbs.json-listed chapters that may not match
    # the naming regex (e.g. chapter-outline.md). verify() compares every
    # non-target chapter against this baseline, so every chapter must be
    # snapshotted or it would be flagged as an unexpected "new" change.
    wbs_chapters = {c.get("filename") for c in _as_list(wbs.get("chapters"), "wbs.chapters")
                    if isinstance(c, dict) and isinstance(c.get("filename"), str)}
    for path in sorted(output_dir.iterdir()) if output_dir.exists() else []:
        if path.is_file() and (path.name in wbs_chapters or _is_chapter_file(path.name)):
            state["chapter_hashes"][path.name] = _sha256(path)
    for chapter_file in affected:
        if chapter_file not in state["chapter_hashes"]:
            chapter_path = output_dir / chapter_file
            state["chapter_hashes"][chapter_file] = (
                _sha256(chapter_path) if chapter_path.exists() else None
            )

    for chapter_file in affected:
        title = _chapter_title(wbs, chapter_file)
        src_ids = _collect_chapter_src_ids(drift, trace, chapter_file)
        changed_sources = _collect_changed_sources(drift, set(src_ids))
        chapters_out.append({
            "file": chapter_file,
            "title": title,
            "changed_sources": changed_sources,
            "src_ids": src_ids,
            "prompt_path": str(prompts_dir / chapter_file),
        })

    state["affected_chapters"] = chapters_out
    state_path = incremental_dir / STATE_FILE_NAME
    tmp = state_path.with_name(f"{STATE_FILE_NAME}.tmp")
    _write_new_file(tmp, (json.dumps(state, ensure_ascii=False, indent=1) + "\n")
                    .encode("utf-8"))
    os.replace(tmp, state_path)

    if args.json:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "affected_chapters": chapters_out,
            "prompt_dir": str(prompts_dir),
            "existing_src_ids_count": len(existing_src_ids),
        }, ensure_ascii=False, indent=1))
    else:
        print("Affected chapters:")
        if chapters_out:
            print(f"{'Chapter file':<28} {'Title':<32} Changed sources")
            for ch in chapters_out:
                print(f"{ch['file']:<28} {ch['title'][:32]:<32} "
                      f"{', '.join(ch['changed_sources']) or '-'}")
        else:
            print("  (none)")
        print(f"Prompts written to: {prompts_dir}")
        print(f"State written to:   {state_path}")

    return 2 if not affected else 0


# ---------------------------------------------------------------------------
# verify (shared by verify + apply)
# ---------------------------------------------------------------------------

def _load_state(specback_dir: Path) -> dict:
    state_path = specback_dir / INCREMENTAL_DIR_NAME / STATE_FILE_NAME
    if not state_path.exists():
        _fail(
            f"state.json not found: {state_path} — run 'plan' first",
            code=3,
        )
    state = _load_json_object(state_path, "state.json")
    if state.get("schema_version") != SCHEMA_VERSION:
        _fail(
            f"state.json schema mismatch: expected {SCHEMA_VERSION}, got "
            f"{state.get('schema_version')!r} — re-run 'plan'",
            code=3,
        )
    for ch in _as_list(state.get("affected_chapters"), "state.affected_chapters"):
        if isinstance(ch, dict) and not _valid_chapter_name(ch.get("file")):
            _fail(f"state.json contains invalid chapter filename: {ch.get('file')!r}",
                  code=3)
    return state


def _is_affected_target(state: dict, target: str) -> bool:
    for ch in _as_list(state.get("affected_chapters"), "state.affected_chapters"):
        if isinstance(ch, dict) and ch.get("file") == target:
            return True
    return False


def _missing_src_ids(text: str, source_map: dict) -> list[str]:
    """Every SRC-ID REF in the updated file must exist in source-map.json."""
    existing = {
        u.get("id") for u in _as_list(source_map.get("units"), "source-map.units")
        if isinstance(u, dict)
    }
    found = set()
    for m in SRC_ID_RE.finditer(text):
        found.add(m.group(1))
    return sorted(found - existing)


def _has_any_ref(text: str) -> bool:
    return bool(ALL_REF_RE.search(text))


def _collateral_changes(output_dir: Path, state: dict, target: str,
                        wbs_chapters: set[str]) -> list[str]:
    """Chapter files under output_dir that changed vs baseline (excluding target)."""
    baseline = state.get("chapter_hashes", {})
    changed: list[str] = []
    if not output_dir.exists():
        return changed
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name not in wbs_chapters and not _is_chapter_file(path.name):
            continue
        if path.name == target:
            continue
        if path.name not in baseline:
            # A new chapter file that wasn't snapshotted — treat as unexpected change.
            changed.append(f"{path.name} (new, not in baseline)")
            continue
        expected = baseline.get(path.name)
        if expected is None:
            changed.append(f"{path.name} (baseline missing)")
            continue
        if _sha256(path) != expected:
            changed.append(path.name)
    return changed


def _run_verify_checks(
    specback_dir: Path,
    output_dir: Path,
    updated_bytes: bytes,
    target: str,
) -> tuple[dict, str]:
    """Run all verify checks on the exact bytes that apply will write.

    *updated_bytes* is read once by the caller (TOCTOU-safe) and both the
    SRC-ID scan and the apply write operate on the same bytes.
    """
    state = _load_state(specback_dir)
    source_map_path = specback_dir / "source-map.json"
    source_map = _load_json_object(source_map_path, "source-map.json")

    text = updated_bytes.decode("utf-8", errors="replace")
    target_ok = _is_affected_target(state, target)
    missing = _missing_src_ids(text, source_map) if target_ok else []
    has_ref = _has_any_ref(text)

    wbs_path = specback_dir / "wbs.json"
    wbs = _load_json_object(wbs_path, "wbs.json")
    wbs_chapters = {
        c.get("filename")
        for c in _as_list(wbs.get("chapters"), "wbs.chapters")
        if isinstance(c, dict) and isinstance(c.get("filename"), str)
    }
    wbs_chapters_set: set[str] = {c for c in wbs_chapters if isinstance(c, str)}

    # Target must exist in the output dir for verify to be meaningful.
    target_exists = (output_dir / target).exists()

    collateral: list[str] = []
    unchanged_target = False
    if target_ok:
        collateral = _collateral_changes(output_dir, state, target, wbs_chapters_set)
        baseline = state.get("chapter_hashes", {}).get(target)
        if baseline is not None and hashlib.sha256(updated_bytes).hexdigest() == baseline:
            unchanged_target = True

    warnings: list[str] = []
    if target_ok and unchanged_target:
        warnings.append("target chapter unchanged vs baseline")
    if target_ok and not has_ref:
        warnings.append("updated chapter contains no <!-- REF: ... --> markers")

    result = {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "target_affected": target_ok,
        "target_exists_in_output": target_exists,
        "src_id_check": {
            "missing_count": len(missing),
            "missing": missing,
        },
        "collateral_check": {
            "changed_unexpected": collateral,
            "unchanged_target": unchanged_target,
        },
        "warnings": warnings,
        "passed": (
            target_ok
            and target_exists
            and not missing
            and not collateral
        ),
    }
    return result, target


def _print_verify_human(result: dict) -> None:
    print(f"Target: {result['target']}")
    print(f"  Target is an affected chapter: {'OK' if result['target_affected'] else 'FAIL'}")
    if result["target_affected"]:
        print(f"  Target exists in output dir: "
              f"{'OK' if result['target_exists_in_output'] else 'FAIL'}")
        missing = result["src_id_check"]["missing"]
        if missing:
            print(f"  SRC-ID existence: FAIL ({', '.join(missing)})")
        else:
            print("  SRC-ID existence: OK")
        collateral = result["collateral_check"]["changed_unexpected"]
        if collateral:
            print(f"  Zero collateral edits: FAIL ({', '.join(collateral)})")
        else:
            print("  Zero collateral edits: OK")
        for w in result.get("warnings", []):
            print(f"  WARNING: {w}")
    print(f"Result: {'PASS' if result['passed'] else 'FAIL'}")


def cmd_verify(args: argparse.Namespace) -> int:
    if not args.updated:
        _fail("--updated PATH is required for verify")
    updated_path = Path(args.updated)
    if not updated_path.exists():
        _fail(f"updated chapter not found: {updated_path}")
    try:
        updated_bytes = updated_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read updated chapter: {updated_path}: {exc}")
    result, _target = _run_verify_checks(
        Path(args.specback_dir), Path(args.output_dir), updated_bytes,
        updated_path.name,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        _print_verify_human(result)
    return 0 if result["passed"] else 1


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace) -> int:
    if not args.updated:
        _fail("--updated PATH is required for apply")
    updated_path = Path(args.updated)
    if not updated_path.exists():
        _fail(f"updated chapter not found: {updated_path}")
    try:
        updated_bytes = updated_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read updated chapter: {updated_path}: {exc}")

    specback_dir = Path(args.specback_dir)
    output_dir = Path(args.output_dir)
    target = updated_path.name

    result, target = _run_verify_checks(specback_dir, output_dir, updated_bytes, target)
    if not result["passed"]:
        if args.json:
            print(json.dumps({"applied": False, "verify": result},
                             ensure_ascii=False, indent=1))
        else:
            _print_verify_human(result)
            print("Apply aborted: verification failed")
        return 1

    target_path = output_dir / target
    if not target_path.exists():
        _fail(f"target chapter not found in output dir: {target_path}")

    # 1) Backup (refuse symlinked/stale backups; warn when one already exists).
    backup_path = target_path.with_name(f"{target}.pre-incremental")
    if target_path.exists():
        if backup_path.exists():
            if backup_path.is_symlink():
                _fail(f"refusing backup: {backup_path} is a symlink")
            print(f"warning: backup already exists, keeping it: {backup_path}",
                  file=sys.stderr)
        else:
            shutil.copy2(target_path, backup_path)
            os.chmod(backup_path, 0o600)
            print(f"backup: {backup_path}", file=sys.stderr)

    # 2) Atomic replace (same bytes that were verified — TOCTOU-safe).
    tmp_path = target_path.with_name(f".{target}.tmp")
    _write_new_file(tmp_path, updated_bytes)
    os.replace(tmp_path, target_path)
    if not args.json:
        print(f"replaced: {target_path}")

    # 3) Refresh trace (unless skipped). NOTE: the chapter is replaced BEFORE
    #    trace refresh; on build-trace failure the .pre-incremental backup is
    #    the recovery path (apply exits 1 and reports the failure).
    if not args.skip_trace_refresh:
        bt = _script_dir() / "build-trace.py"
        if not bt.exists():
            _fail(f"build-trace.py not found: {bt} (use --skip-trace-refresh to bypass)")
        proc = subprocess.run(
            [
                sys.executable, str(bt),
                "--specback-dir", str(specback_dir),
                "--output-dir", str(specback_dir),
                "--target-dir-for-required", str(output_dir.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip(), file=sys.stderr)
            _fail(f"build-trace.py failed (exit {proc.returncode}); "
                  f"recover from backup: {backup_path}")
        if not args.json:
            print(f"trace refreshed: {specback_dir / 'trace.json'}")

    if args.json:
        backup_reported = str(backup_path) if backup_path.exists() else None
        print(json.dumps({"applied": True, "target": target,
                          "backup": backup_reported}, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--specback-dir", default=".specback",
                   help="Directory holding wbs.json / source-map.json / trace.json")
    p.add_argument("--output-dir", default=".",
                   help="Where final spec chapters live (default: .)")
    p.add_argument("--drift-report", default=None,
                   help="Drift report input (default: {specback-dir}/drift-report.json)")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable stdout for plan/verify/apply")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="specback-incremental-update.py",
        description="Incremental spec update: plan / verify / apply (Issue #269)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Identify affected chapters and generate prompts")
    _add_common_args(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_verify = sub.add_parser("verify", help="Validate an updated chapter")
    _add_common_args(p_verify)
    p_verify.add_argument("--updated", default=None,
                          help="Path to the re-investigated chapter file")
    p_verify.set_defaults(func=cmd_verify)

    p_apply = sub.add_parser("apply", help="Verify then atomically apply an updated chapter")
    _add_common_args(p_apply)
    p_apply.add_argument("--updated", default=None,
                         help="Path to the re-investigated chapter file")
    p_apply.add_argument("--skip-trace-refresh", action="store_true",
                         help="Do not run build-trace.py after replacing the chapter")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    if args.drift_report is None:
        args.drift_report = str(Path(args.specback_dir) / "drift-report.json")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
