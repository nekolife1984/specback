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
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

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
