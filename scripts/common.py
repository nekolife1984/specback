#!/usr/bin/env python3
"""
common.py — shared micro-helpers for specback scripts (Issue #279).

Consolidates the cross-cutting helpers that were previously re-implemented
separately in nearly every script:

- UTC timestamps: ``datetime.now(timezone.utc).isoformat()`` (~22 copies)
- JSON loading: ``load_json`` / ``_load_json`` / ``_load_json_object``
  (~7 different implementations)
- SHA-256 hashing: ``_sha256`` / ``_sha256_file`` / ``_sha256_dir_sorted`` /
  ``_hash_file`` (~6 implementations)
- ``--specback-dir`` argparse boilerplate (~16 copies; defaults vary:
  ``.specback`` vs ``Path.cwd() / '.specback'`` vs ``None``)

This module only defines the foundation; wiring individual scripts onto
these helpers happens in follow-up issues
(``chore/consolidate-io-helpers`` / ``chore/consolidate-ref-utils`` / …).

Design notes
------------
- ``load_json_text`` raises ``OSError`` / ``ValueError`` on failure and
  NEVER calls ``sys.exit`` — each caller decides how to handle a missing or
  corrupt file (pre-check, catch, or wrap in a clean ``_fail`` helper).
  Shared modules must stay import-safe: ``SystemExit`` is a
  ``BaseException`` that slips past ``except Exception`` and kills library
  consumers such as MCP servers (#270).
- The hardened behaviour (bounded reads, non-finite JSON rejection, symlink
  refusal, atomic writes) follows the direction already taken by
  ``specback-estimate.py`` / ``specback-health.py`` (#267/#273): corrupt or
  hostile data fails loudly instead of propagating.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Guard against unbounded reads (same cap as specback-health.py).
MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB
#: Cap for auxiliary JSON artifacts (same cap as specback-health.py).
MAX_JSON_BYTES = 5 * 1024 * 1024  # 5 MiB

_SHA_CHUNK = 64 * 1024


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Equivalent to ``datetime.now(timezone.utc).isoformat()`` — the pattern
    previously duplicated in ~22 places across the scripts.
    """
    return datetime.now(timezone.utc).isoformat()


def _reject_nonfinite(name: str) -> Any:
    """``json.loads`` parse_constant hook — reject NaN/Infinity as invalid JSON."""
    raise ValueError(f"non-finite JSON constant: {name}")


def _read_text_bounded(path: Path, max_bytes: int = MAX_INPUT_BYTES) -> str:
    """Read a regular file with a byte cap; reject special files / symlinks.

    Mirrors ``specback-health.read_text_bounded`` (#267/#270 hardening):
    ``is_file()`` is False for FIFOs, sockets and devices (closes the hang
    vector), and ``O_NOFOLLOW`` rejects a symlinked final component where the
    platform supports it. Unlike the chapter-text variant, decoding is
    strict: invalid UTF-8 in a JSON artifact should raise, not silently
    replace.
    """
    if not path.is_file():
        raise OSError(f"not a regular file: {path}")
    flags = getattr(os, "O_NOFOLLOW", 0) | os.O_RDONLY
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as f:
            data = f.read(max_bytes + 1)
    finally:
        fd = -1
    if len(data) > max_bytes:
        raise ValueError(f"{path}: exceeds {max_bytes} bytes")
    return data.decode("utf-8")


def load_json_text(path: str | Path, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    """Read and parse the JSON document at *path*.

    Raises
        FileNotFoundError — *path* does not exist (callers branch on this
            to choose their own missing-file behaviour)
        OSError          — *path* is not a regular file / cannot be opened
        ValueError       — content exceeds *max_bytes*, is not valid JSON,
                           or contains non-finite constants (NaN/Infinity)

    Never exits the process; shared modules must stay import-safe (#270).
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"no such file: {target}")
    return json.loads(
        _read_text_bounded(target, max_bytes=max_bytes),
        parse_constant=_reject_nonfinite,
    )


def atomic_write_json(path: str | Path, obj: Any) -> None:
    """Atomically write *obj* as pretty JSON to *path*.

    Uses ``ensure_ascii=False, indent=2`` (plus a trailing newline) so CJK
    text stays readable, matching ``specback-estimate.record_actual``.
    Writes to a unique temp file in the same directory and ``os.replace``s
    it into place, so concurrent readers never observe a partially written
    file. Refuses to write through a symlink (arbitrary-file-overwrite
    guard, #273). The temp file is removed on failure.
    """
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"refusing to write through symlink: {target}")
    tmp = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def add_specback_dir_arg(
    parser: argparse.ArgumentParser,
    *,
    default: str | Path = ".specback",
) -> None:
    """Add the standard ``--specback-dir`` argument to *parser*.

    Always ``type=Path`` so every caller receives a ``Path`` (the scripts
    currently mix ``str``, ``Path`` and ``None`` defaults). The default
    resolves relative to the caller's cwd, matching the ``.specback``
    convention used throughout the docs.
    """
    parser.add_argument(
        "--specback-dir",
        type=Path,
        default=Path(default),
        help=f"Path to .specback/ directory (default: {default})",
    )
