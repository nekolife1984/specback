#!/usr/bin/env python3
"""
git_utils.py — shared, safe git helpers for specback scripts.

The helpers here exist to keep git subprocess commands safe from argument
injection. A user-controlled "base" value (``--base`` CLI argument or
``state.json.generated_at_commit``) is passed straight into a ``git diff
<base>`` command; without validation a value like ``--output=/tmp/x`` is
interpreted by git as an option, allowing arbitrary file creation /
overwrite (Issue #253).

Rule: any git command that accepts a ref-like value MUST resolve it through
:func:`resolve_ref` before building the argv. The resolved value is always a
commit hash, which is safe to pass as a positional argument.

Beyond :func:`resolve_ref`, this module hosts the shared git diff runner
(:func:`run_git_diff`), the ``--name-status`` parser (:func:`parse_diff_name_status`)
and the common base-ref resolution (:func:`resolve_base`) that used to be
duplicated between detect-drift.py and fix-refs.py (Issue #282).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from common import load_json_text_or

# Characters allowed in a git ref. Deliberately restrictive: rejects
# option-like values (``-`` prefix is also checked explicitly) and anything
# git might treat as a flag or path separator trickery.
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


def resolve_ref(base: str, cwd: str | Path | None = None) -> str:
    """Validate *base* and resolve it to a commit hash.

    ``base`` must not start with ``-`` (git would treat it as an option —
    e.g. ``--output=/tmp/x`` — enabling argument injection) and must only
    contain ref-safe characters. The resolved hash is what ``git diff``
    commands should run against.

    Exits with status 1 (after printing an error to stderr) when *base* is
    invalid or cannot be resolved in *cwd*.
    """
    if not base or base.startswith("-") or not SAFE_REF_RE.match(base):
        print(f"ERROR: invalid git ref: {base!r}", file=sys.stderr)
        sys.exit(1)
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    if resolved.returncode != 0:
        print(f"ERROR: cannot resolve git ref: {base}", file=sys.stderr)
        sys.exit(1)
    return resolved.stdout.strip()


def run_git_diff(
    base: str,
    *args: str,
    cwd: str | Path | None = None,
) -> str:
    """Run ``git diff <extra args> <base>`` and return the diff text.

    *args (e.g. ``--name-status``, ``-U0``) are inserted between ``git
    diff`` and the resolved commit hash so each caller keeps its exact
    output format and exit behaviour.  ``base`` goes through
    :func:`resolve_ref` first (argument-injection guard, Issue #253).

    *args must be caller-supplied constants only — they are passed to git
    verbatim, so user-controlled values here would reintroduce option
    injection.

    Exits with status 1 (after printing an error to stderr) when git
    diff fails.
    """
    resolved = resolve_ref(base, cwd)
    result = subprocess.run(
        ["git", "diff", *args, resolved],
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


def parse_diff_name_status(text: str) -> list[dict[str, str]]:
    """Parse ``git diff --name-status`` output text into entry dicts.

    Handles all git status codes, including rename (R) and copy (C), which
    produce three tab-separated fields: status, old_path, new_path.  For
    R/C entries the *new* path is reported as ``file`` and the old path as
    ``old_file``.  Lines that do not match the format are skipped.
    """
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]  # first char: A/M/D/R/C/...
        if len(parts) >= 3:
            # R<similarity>\told/path\tnew/path ; C<similarity>\told\tnew
            entries.append({
                "status": status,
                "file": parts[2],          # new path
                "old_file": parts[1],       # old path
            })
        else:
            entries.append({"status": status, "file": parts[1]})
    return entries


def resolve_base(args_base: str | None, specback_path: Path) -> str:
    """Determine the git ref to diff against.

    Priority:
    1. Explicit ``--base`` CLI argument
    2. ``state.json.generated_at_commit`` (Phase 6 recorded this)
    3. ``HEAD`` (fallback)
    """
    if args_base is not None:
        return args_base
    state = load_json_text_or(specback_path / "state.json", None)
    if state is not None:
        commit = state.get("generated_at_commit")
        if commit:
            return str(commit)
    return "HEAD"
