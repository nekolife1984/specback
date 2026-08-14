#!/usr/bin/env python3
"""
change-spec.py — Phase 7c: mechanically extract structured change data.

Reads ``git diff --unified`` (git mode) or file hashes (hash mode) and
produces a structured ``change-spec.json`` that the AI agent (Phase 7c)
uses to write the human-readable ``change-spec.md``.

This script is intentionally mechanical — it extracts *facts only*, never
interpretation. All natural-language explanation is left to the AI agent.

Usage
-----
    # Git mode (default)
    python change-spec.py --specback-dir .specback
    python change-spec.py --specback-dir .specback --base HEAD
    python change-spec.py --specback-dir .specback --diff < git-diff-output.txt

    # Hash mode (non-Git projects)
    python change-spec.py --specback-dir .specback --mode hash

    # Explicit mode
    python change-spec.py --specback-dir .specback --mode git

Dependencies
------------
    Python 3.10+ (stdlib only).

Output
------
    .specback/change-spec.json   (structured facts — consumed by AI agent)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
import artifact_io
from common import sha256_file, utcnow_iso
from pathlib import Path
from typing import Any

from git_utils import resolve_ref


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"

MODE_AUTO = "auto"
MODE_GIT = "git"
MODE_HASH = "hash"

# Language-specific symbol definition patterns (regex for diff lines)
# Matches only lines that BEGIN with a definition keyword.
SYMBOL_PATTERNS: dict[str, list[re.Pattern]] = {
    "py": [
        re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
        re.compile(r"^\s*class\s+(\w+)"),
    ],
    "rb": [
        re.compile(r"^\s*(?:def\s+(?:self\.)?(\w+))"),
        re.compile(r"^\s*class\s+(\w+)"),
        re.compile(r"^\s*module\s+(\w+)"),
    ],
    "js": [
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
        re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?[\(\w]"),
    ],
    "ts": [
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
        re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]"),
    ],
    "go": [
        re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(\w+)"),
    ],
    "java": [
        re.compile(r"^\s*(?:public|private|protected|static|\s)*\s+(?:class|interface|enum)\s+(\w+)"),
        re.compile(r"^\s*(?:public|private|protected|static|\s)*\s+\w+\s+(\w+)\s*\("),
    ],
    "cs": [
        re.compile(r"^\s*(?:public|private|protected|internal|static|\s)*\s+(?:class|interface|struct|enum)\s+(\w+)"),
        re.compile(r"^\s*(?:public|private|protected|internal|static|\s)*\s+\w+\s+(\w+)\s*\("),
    ],
    "php": [
        re.compile(r"^\s*(?:public|private|protected|static|\s)*\s+function\s+(\w+)"),
        re.compile(r"^\s*class\s+(\w+)"),
    ],
    "sql": [],
    "cbl": [],
}

# Import/require/include patterns per language
IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "py": [re.compile(r"^\s*(?:import|from)\s+(\S+)")],
    "rb": [re.compile(r"^\s*(?:require|require_relative|include|extend)\s+[\"']?([^\"'\s]+)")],
    "js": [re.compile(r"^\s*(?:import\s+.+\s+from\s+[\"']([^\"']+)|require\s*\(\s*[\"']([^\"']+))")],
    "ts": [re.compile(r"^\s*(?:import\s+.+\s+from\s+[\"']([^\"']+)|require\s*\(\s*[\"']([^\"']+))")],
    "go": [re.compile(r"^\s*\"([^\"]+)\"")],
    "java": [re.compile(r"^\s*import\s+(\S+)")],
    "cs": [re.compile(r"^\s*(?:using\s+(\S+))")],
    "php": [re.compile(r"^\s*(?:use\s+(\S+))")],
    "sql": [],
    "cbl": [],
}


def _ext_from_path(path: str) -> str:
    """Return the file extension without leading dot, or empty string."""
    _, ext = os.path.splitext(path)
    return ext.lstrip(".").lower()


def _detect_symbols(lines: list[str], path: str) -> dict[str, list[str]]:
    """Detect symbol definitions and import changes from a list of diff lines.

    Returns
    -------
    dict with keys ``definitions`` and ``imports``.
    """
    ext = _ext_from_path(path)
    sym_patterns = SYMBOL_PATTERNS.get(ext, [])
    imp_patterns = IMPORT_PATTERNS.get(ext, [])

    definitions: list[str] = []
    imports: list[str] = []

    for line in lines:
        stripped = line.lstrip("+-")
        for pat in sym_patterns:
            m = pat.match(stripped)
            if m:
                name = m.group(1)
                if name not in definitions:
                    definitions.append(name)
                break
        for pat in imp_patterns:
            m = pat.match(stripped)
            if m:
                imp = m.group(1) or m.group(2) or ""
                if imp and imp not in imports:
                    imports.append(imp)
                break

    return {"definitions": definitions, "imports": imports}


# ---------------------------------------------------------------------------
# Diff parsing (git unified format with context)
# ---------------------------------------------------------------------------

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Language-level: match lines that look like complete top-level statements
COMPLETE_STMT_RE = re.compile(r"^\s*(?:end|}\)?;?|```)$")


def parse_unified_diff(diff_text: str) -> dict[str, dict[str, Any]]:
    """Parse unified diff text and extract per-file change data.

    Returns
    -------
    dict mapping file path ``{"path": ..., "status": ..., "insertions": N,
    "deletions": N, "added_lines": [...], "removed_lines": [...],
    "context": [...]}``
    """
    files: dict[str, dict[str, Any]] = {}
    current_file: str | None = None
    current_status: str = "M"

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # Extract path from "diff --git a/path b/path"
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]  # "b/path"
                current_file = b_path[2:] if b_path.startswith("b/") else b_path
                # Detect new/deleted from "new file" / "deleted file" markers
                current_status = "M"
            continue

        if line.startswith("new file mode"):
            current_status = "A"
            continue
        if line.startswith("deleted file mode"):
            current_status = "D"
            continue
        if line.startswith("rename from "):
            current_status = "R"
            continue

        if line.startswith("--- a/"):
            continue
        if line.startswith("+++ b/"):
            continue
        if line.startswith("index ") or line.startswith("similarity index"):
            continue
        if line.startswith("---"):
            continue

        if current_file is None:
            continue

        if current_file not in files:
            files[current_file] = {
                "file": current_file,
                "status": current_status,
                "insertions": 0,
                "deletions": 0,
                "added_lines": [],
                "removed_lines": [],
                "context_lines": [],
                "hunks": [],
            }

        m = HUNK_HEADER_RE.match(line)
        if m:
            files[current_file]["hunks"].append({
                "old_start": int(m.group(1)),
                "old_count": int(m.group(2)) if m.group(2) else 1,
                "new_start": int(m.group(3)),
                "new_count": int(m.group(4)) if m.group(4) else 1,
            })
            continue

        if line.startswith("+") and not line.startswith("+++"):
            files[current_file]["insertions"] += 1
            files[current_file]["added_lines"].append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            files[current_file]["deletions"] += 1
            files[current_file]["removed_lines"].append(line[1:])
        elif line.startswith(" "):
            # Context line
            files[current_file]["context_lines"].append(line[1:])

    return files


# ---------------------------------------------------------------------------
# Structural analysis
# ---------------------------------------------------------------------------


def analyze_structural_changes(
    file_data: dict[str, Any],
) -> dict[str, Any]:
    """Analyze added/removed lines for structural changes.

    Returns
    -------
    dict with ``added_symbols``, ``removed_symbols``, ``modified_symbols``,
    ``added_imports``, ``removed_imports``.
    """
    path = file_data["file"]

    added = file_data.get("added_lines", [])
    removed = file_data.get("removed_lines", [])

    added_info = _detect_symbols(added, path)
    removed_info = _detect_symbols(removed, path)

    # Compute modified symbols (present in both added and removed)
    added_defs = set(added_info["definitions"])
    removed_defs = set(removed_info["definitions"])
    modified = sorted(added_defs & removed_defs)
    only_added = sorted(added_defs - removed_defs)
    only_removed = sorted(removed_defs - added_defs)

    # Compute import changes
    added_imports = set(added_info["imports"])
    removed_imports = set(removed_info["imports"])
    net_added_imports = sorted(added_imports - removed_imports)
    net_removed_imports = sorted(removed_imports - added_imports)

    return {
        "added_symbols": only_added,
        "removed_symbols": only_removed,
        "modified_symbols": modified,
        "added_imports": net_added_imports,
        "removed_imports": net_removed_imports,
    }


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------


def extract_snippets(
    file_data: dict[str, Any],
    max_context: int = 3,
) -> dict[str, str]:
    """Build before/after code snippets from diff context.

    Returns
    -------
    dict with ``before_snippet`` and ``after_snippet`` strings,
    or empty strings if no meaningful change.
    """
    removed = file_data.get("removed_lines", [])
    added = file_data.get("added_lines", [])
    context = file_data.get("context_lines", [])
    hunks = file_data.get("hunks", [])

    if not removed and not added:
        return {"before_snippet": "", "after_snippet": ""}

    # Build before snippet: removed lines + context around them
    before_parts: list[str] = []
    after_parts: list[str] = []

    # Simple approach: use first hunk's context + removed/added
    for i, hunk in enumerate(hunks[:3]):  # max 3 hunks for snippet
        if i == 0:
            ctx_before = context[:max_context]
            before_parts.extend(ctx_before)
            before_parts.extend(removed[:20])  # cap at 20 lines
            after_parts.extend(ctx_before)
            after_parts.extend(added[:20])

    snippet_before = "\n".join(before_parts) if before_parts else ""
    snippet_after = "\n".join(after_parts) if after_parts else ""

    return {
        "before_snippet": snippet_before,
        "after_snippet": snippet_after,
    }


# ---------------------------------------------------------------------------
# Load artifacts (reused patterns from detect-drift.py)
# ---------------------------------------------------------------------------


def load_source_map(path: Path) -> dict[str, Any]:
    """Load source-map.json or return empty on missing.

    Delegates index construction to :func:`artifact_io.load_source_map`
    (Issue #283); the missing-file policy (empty structure) is unchanged.
    """
    if not path.exists():
        return {"units": [], "by_path": {}, "by_id": {}, "stats": {}, "target_root": ""}
    return artifact_io.load_source_map(path, build_indexes=True)


def load_trace(path: Path) -> dict[str, Any]:
    """Load trace.json or return empty on missing.

    Delegates to :func:`artifact_io.load_trace` (Issue #283); the
    missing-file policy (``{"by_source": {}}``) is unchanged.
    """
    trace = artifact_io.load_trace(path)
    return trace if trace is not None else {"by_source": {}}


def load_state(path: Path) -> dict[str, Any] | None:
    """Load state.json if it exists.

    Delegates to :func:`artifact_io.load_state` (Issue #283).
    """
    return artifact_io.load_state(path)


def load_source_hashes(path: Path) -> dict[str, Any]:
    """Load source-hashes.json or return empty."""
    if not path.exists():
        return {"units": {}, "target_root": "."}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Cross-reference helpers
# ---------------------------------------------------------------------------


def cross_reference_sections(
    file_path: str,
    status: str,
    source_map: dict[str, Any],
    trace: dict[str, Any],
) -> list[dict[str, str]]:
    """Look up spec sections that reference this source file.

    Returns
    -------
    list of ``{"file": ..., "section": ...}``
    """
    by_path = source_map.get("by_path", {})
    by_source = trace.get("by_source", {})

    src_units = by_path.get(file_path, [])
    sections: list[dict[str, str]] = []
    seen: set[str] = set()

    for unit in src_units:
        sid = unit.get("id", "")
        trace_entry = by_source.get(sid)
        if not trace_entry:
            continue
        for sec in trace_entry.get("covered_by_sections", []):
            key = f"{sec.get('file', '')}::{sec.get('section', '')}"
            if key not in seen:
                seen.add(key)
                sections.append({
                    "file": sec.get("file", ""),
                    "section": sec.get("section", ""),
                })

    return sections


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_git_unified_diff(
    base: str,
    cwd: str | Path | None = None,
    context_lines: int = 5,
) -> str:
    """Run ``git diff -U<context_lines> <base>`` and return raw text."""
    # Resolve base to a validated commit hash before building argv —
    # prevents git option injection via --base / state.json (Issue #253).
    base = resolve_ref(base, cwd)
    cmd = ["git", "diff", f"-U{context_lines}", base]
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


def run_git_diff_name_status(
    base: str,
    cwd: str | Path | None = None,
) -> list[dict[str, str]]:
    """Run ``git diff --name-status <base>`` and return parsed entries."""
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
            f"ERROR: git diff --name-status failed:\n{result.stderr.strip()}",
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
        status = parts[0][0]
        if status == "R" and len(parts) >= 3:
            entries.append({
                "status": status,
                "file": parts[2],
                "old_file": parts[1],
            })
        else:
            entries.append({"status": status, "file": parts[1]})
    return entries


# ---------------------------------------------------------------------------
# Hash mode
# ---------------------------------------------------------------------------


def compute_hash_changes(
    source_hashes: dict[str, Any],
    source_map: dict[str, Any],
    target_root: str,
) -> list[dict[str, str]]:
    """Detect changed files via hash comparison (reused from detect-drift.py)."""

    def _hash_file(path: Path) -> str:
        try:
            return sha256_file(path)
        except (FileNotFoundError, OSError):
            return ""

    changes: list[dict[str, str]] = []
    units = source_hashes.get("units", {})
    seen_paths: set[str] = set()

    for sid, stored in units.items():
        file_path_rel = stored.get("path", "")
        stored_hash = stored.get("hash", "")
        seen_paths.add(file_path_rel)
        abs_path = Path(target_root) / file_path_rel

        if stored.get("status") == "MISSING" or not abs_path.exists():
            changes.append({"status": "D", "file": file_path_rel})
            continue

        current_hash = _hash_file(abs_path)
        if f"sha256:{current_hash}" != stored_hash and current_hash:
            changes.append({"status": "M", "file": file_path_rel})

    sm_units = source_map.get("units", [])
    sm_paths: set[str] = {u.get("path", "") for u in sm_units if u.get("path")}
    for sm_path in sm_paths:
        if sm_path and sm_path not in seen_paths:
            changes.append({"status": "A", "file": sm_path})

    return changes


def read_current_file(target_root: str, rel_path: str) -> str:
    """Read current file content (hash mode, no before version available)."""
    try:
        return Path(target_root, rel_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def analyze_current_structure(file_path: str, content: str) -> dict[str, Any]:
    """Extract structural info from current file (hash mode only).

    Returns
    -------
    dict with ``current_symbols`` and ``current_imports``.
    """
    lines = content.splitlines()
    info = _detect_symbols(lines, file_path)
    return {
        "current_symbols": info["definitions"],
        "current_imports": info["imports"],
    }


# ---------------------------------------------------------------------------
# Resolve mode and base (reused pattern from detect-drift.py)
# ---------------------------------------------------------------------------


def resolve_mode(args_mode: str | None, specback_path: Path) -> str:
    """Determine detection mode — same logic as detect-drift.py."""
    if args_mode is not None:
        return args_mode

    has_git = (specback_path.parent / ".git").is_dir()
    state = load_state(specback_path / "state.json")
    has_generated_commit = bool(state and state.get("generated_at_commit"))
    has_source_hashes = (specback_path / "source-hashes.json").exists()

    if has_git and has_generated_commit:
        return MODE_GIT
    if has_source_hashes:
        return MODE_HASH
    if has_git:
        return MODE_GIT

    print(
        "ERROR: cannot determine detection mode. "
        "Run from a Git repo or create source-hashes.json first. "
        "Pass --mode git or --mode hash explicitly.",
        file=sys.stderr,
    )
    sys.exit(1)


def resolve_base(args_base: str | None, specback_path: Path) -> str:
    """Determine git ref to diff against."""
    if args_base is not None:
        return args_base
    state = load_state(specback_path / "state.json")
    if state is not None:
        commit = state.get("generated_at_commit")
        if commit:
            return str(commit)
    return "HEAD"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_change_spec(
    specback_path: Path,
    mode: str,
    base: str,
    diff_text: str | None,
) -> dict[str, Any]:
    """Build the change-spec.json data structure."""
    ts = utcnow_iso()

    # Load shared artifacts
    source_map = load_source_map(specback_path / "source-map.json")
    trace = load_trace(specback_path / "trace.json")
    target_root = source_map.get("target_root", specback_path.parent)

    # --- Phase 1: get list of changed files ---
    name_status_changes: list[dict[str, str]] = []
    unified_diff_text: str = ""

    if mode == MODE_GIT:
        if diff_text is not None:
            # Parse inline diff for name status (first pass)
            name_status_changes = _parse_name_status_from_unified(diff_text)
            unified_diff_text = diff_text
        else:
            resolved_base = resolve_base(base, specback_path)
            name_status_changes = run_git_diff_name_status(
                resolved_base, cwd=str(specback_path.parent),
            )
            unified_diff_text = run_git_unified_diff(
                resolved_base, cwd=str(specback_path.parent),
            )
    elif mode == MODE_HASH:
        source_hashes = load_source_hashes(specback_path / "source-hashes.json")
        target_root = source_hashes.get("target_root", target_root)
        name_status_changes = compute_hash_changes(source_hashes, source_map, str(target_root))

    if not name_status_changes:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "base": base or "HEAD",
            "generated_at": ts,
            "summary": {
                "total_files": 0,
                "insertions": 0,
                "deletions": 0,
                "by_type": {},
            },
            "files": [],
        }

    # --- Phase 2: parse unified diff for git mode ---
    parsed_files: dict[str, dict[str, Any]] = {}
    if mode == MODE_GIT and unified_diff_text:
        parsed_files = parse_unified_diff(unified_diff_text)
    elif mode == MODE_HASH:
        # For hash mode, we only have current file content
        for change in name_status_changes:
            fpath = change["file"]
            content = read_current_file(str(target_root), fpath)
            parsed_files[fpath] = {
                "file": fpath,
                "status": change["status"],
                "insertions": 0,
                "deletions": 0,
                "added_lines": [],
                "removed_lines": [],
                "context_lines": content.splitlines(),
                "hunks": [],
            }

    # --- Phase 3: build per-file entries ---
    files_output: list[dict[str, Any]] = []
    total_insertions = 0
    total_deletions = 0

    for change in name_status_changes:
        fpath = change["file"]
        status = change["status"]
        file_data = parsed_files.get(fpath, {})

        insertions = file_data.get("insertions", 0)
        deletions = file_data.get("deletions", 0)
        total_insertions += insertions
        total_deletions += deletions

        # Structural analysis
        if mode == MODE_GIT:
            structural = analyze_structural_changes(file_data)
            snippets = extract_snippets(file_data)
        else:
            # Hash mode: report current structure with confidence marker
            content = read_current_file(str(target_root), fpath)
            cur = analyze_current_structure(fpath, content)
            structural = {
                "added_symbols": [],
                "removed_symbols": [],
                "modified_symbols": [],
                "added_imports": [],
                "removed_imports": [],
                "current_symbols_HASH_ONLY": cur["current_symbols"],
                "current_imports_HASH_ONLY": cur["current_imports"],
            }
            snippets = {"before_snippet": "", "after_snippet": content[:500] if content else ""}

        # Cross-reference with source-map / trace
        impacted_sections = cross_reference_sections(fpath, status, source_map, trace)

        entry: dict[str, Any] = {
            "file": fpath,
            "status": status,
            "insertions": insertions,
            "deletions": deletions,
            "structural_changes": structural,
            "before_snippet": snippets.get("before_snippet", ""),
            "after_snippet": snippets.get("after_snippet", ""),
            "impacted_sections": impacted_sections,
        }

        files_output.append(entry)

    # --- Phase 4: build summary ---
    by_type: dict[str, int] = defaultdict(int)
    for entry in files_output:
        by_type[entry["status"]] += 1

    # Classify by change type (simple heuristic based on status)
    type_labels: dict[str, str] = {
        "A": "feature",
        "M": "modification",
        "D": "delete",
        "R": "rename",
        "C": "copy",
    }

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "base": base or "HEAD",
        "generated_at": ts,
        "summary": {
            "total_files": len(files_output),
            "insertions": total_insertions,
            "deletions": total_deletions,
            "by_type": dict(by_type),
        },
        "files": files_output,
    }

    return result


def _parse_name_status_from_unified(diff_text: str) -> list[dict[str, str]]:
    """Extract name-status-like info from a unified diff text."""
    entries: list[dict[str, str]] = []
    current_file: str | None = None
    current_status: str = "M"

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                current_file = b_path[2:] if b_path.startswith("b/") else b_path
                current_status = "M"
        elif line.startswith("new file mode"):
            current_status = "A"
        elif line.startswith("deleted file mode"):
            current_status = "D"
        elif line.startswith("rename from "):
            current_status = "R"
        elif line.startswith("+++ ") and current_file:
            # Normal: "+++ b/path". Deleted: "+++ /dev/null"
            entries.append({"status": current_status, "file": current_file})

    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="specback ChangeSpec: mechanically extract structured change data",
    )
    parser.add_argument(
        "--specback-dir",
        default=".specback",
        help="Path to .specback/ directory (default: .specback)",
    )
    parser.add_argument(
        "--mode",
        default=None,
        choices=[MODE_AUTO, MODE_GIT, MODE_HASH],
        help="Detection mode: auto (default), git, or hash",
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
        help="Raw unified git diff text (for CI use; omit to run git diff automatically)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for change-spec.json (default: <specback-dir>/change-spec.json)",
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

    mode = resolve_mode(args.mode, specback_path)
    base = args.base or "HEAD"

    print(f"change-spec.py: mode={mode}, base={base}", file=sys.stderr)

    result = build_change_spec(
        specback_path=specback_path,
        mode=mode,
        base=base,
        diff_text=args.diff,
    )

    output_path = args.output or str(specback_path / "change-spec.json")
    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"change-spec.py: written to {output_path}", file=sys.stderr)

    summary = result["summary"]
    print(
        f"change-spec.py: {summary['total_files']} files, "
        f"{summary['insertions']} insertions, "
        f"{summary['deletions']} deletions",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
