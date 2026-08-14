#!/usr/bin/env python3
"""
common.py — shared micro-helpers for specback scripts.

Several scripts previously re-implemented the same tiny helpers with subtly
different behaviour: UTC timestamps, safe JSON read/write, SHA-256 digests and
the ubiquitous ``--specback-dir`` argparse block.  This module is the single
home for those helpers so callers get one consistent implementation (Issue
#279).

All helpers are stdlib-only and behave identically to the original
per-script implementations.

Design notes
------------
* ``utcnow_iso`` / ``sha256_file`` are pure and stateless.
* ``load_json_text`` lets the *caller* decide what a missing file means: it
  simply propagates ``FileNotFoundError`` (plus ``json.JSONDecodeError`` /
  ``ValueError`` for malformed or non-finite content), so each script chooses
  to fail, warn, or substitute a default.
* ``atomic_write_json`` writes via a temp file + ``os.replace`` so a reader
  never observes a partially-written file, and it refuses (``allow_nan=False``)
  to emit non-finite constants, which JSON consumers elsewhere in this repo
  already reject.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _reject_nonfinite(value: str) -> Any:
    """Reject NaN / Infinity / -Infinity JSON constants (argparse hook)."""
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def load_json_text(path: str | Path) -> Any:
    """Read and parse *path* as JSON, returning the decoded value.

    Raises ``FileNotFoundError`` if *path* is missing, ``UnicodeDecodeError``
    for non-UTF-8 bytes, ``json.JSONDecodeError`` for malformed JSON and
    ``ValueError`` for non-finite constants.  The caller decides how to handle
    absence/failure.
    """
    with open(path, "rb") as fh:
        return json.loads(
            fh.read().decode("utf-8"),
            parse_constant=_reject_nonfinite,
        )


def load_json_text_or(path: str | Path, default: Any) -> Any:
    """``load_json_text`` that returns *default* when *path* is missing/malformed.

    Convenience helper for callers whose "missing file" behaviour is simply to
    substitute a fallback value (e.g. ``{}``).
    """
    try:
        return load_json_text(path)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError,
            ValueError):
        return default


def atomic_write_json(path: str | Path, obj: Any) -> None:
    """Atomically write *obj* to *path* as JSON (``ensure_ascii=False, indent=2``).

    The JSON is written to a sibling temp file and moved into place with
    ``os.replace`` so readers never see a partial write.  Non-finite JSON
    constants are rejected (``allow_nan=False``) to match the reader-side
    guard in :func:`load_json_text`.
    """
    dest = Path(path)
    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        _dump_json(fh, obj)
        fh.write("\n")
    os.replace(tmp, dest)


def _dump_json(fh: TextIO, obj: Any) -> None:
    json.dump(obj, fh, ensure_ascii=False, indent=2, allow_nan=False)


def sha256_file(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file (chunked, memory-friendly)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def add_specback_dir_arg(parser: Any, *,
                         default: str | Path | None = ".specback") -> None:
    """Add a ``--specback-dir`` argument to *parser*.

    The original scripts used inconsistent defaults (``".specback"``,
    ``Path.cwd() / ".specback"``, ``None``).  This helper centralises them:
    the value is parsed as a ``Path`` with the given *default*.  Pass
    ``default=None`` explicitly if a caller wants to disambiguate at runtime.
    """
    parser.add_argument(
        "--specback-dir",
        type=Path,
        default=Path(default) if default is not None else None,
        help="Path to the specback data directory (crafts/drafts/final, "
             "trace.json, questions.json, etc.).",
    )
