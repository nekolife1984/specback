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
  ``UnicodeDecodeError`` / ``ValueError`` for malformed, non-UTF-8 or
  non-finite content), so each script chooses to fail, warn, or substitute
  a default.
* ``atomic_write_json`` / ``atomic_write_text`` write via a temp file +
  ``os.replace`` so a reader never observes a partially-written file, and
  the JSON writer refuses (``allow_nan=False``) to emit non-finite
  constants, which JSON consumers elsewhere in this repo already reject.
* ``sanitize_control`` replaces C0 control characters in repo-controlled
  strings before they are interpolated into generated reports, preventing
  ANSI/terminal-escape injection.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (``+00:00``, µs)."""
    return datetime.now(timezone.utc).isoformat()


def utcnow_iso_z() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (second precision).

    Used by human-readable reports / database columns that historically used
    ``strftime("%Y-%m-%dT%H:%M:%SZ")``.  Centralising it here removes the
    duplicated format strings.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reject_nonfinite(value: str) -> Any:
    """Reject NaN / Infinity / -Infinity JSON constants (``parse_constant`` hook).

    Public so that callers performing their own bounded ``json.loads`` (e.g.
    with a size limit) can reuse the same guard instead of copying it.
    """
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def sanitize_control(s: str) -> str:
    """Replace C0 control characters with escaped ``\\xNN`` forms.

    Repo-controlled strings (file names, section names, reasons, git refs)
    may carry ANSI/terminal escape sequences (``\\x1b[2J``, ``\\x1b]0;...``
    etc.).  When such strings are interpolated into generated Markdown
    reports that are opened in a terminal or CI log, the escapes execute and
    can spoof prompts or pollute scrollback.  Apply this helper at every
    report interpolation point so an escape can never reach the output.
    """
    return re.sub(
        r"[\x00-\x1f\x7f]",
        lambda m: f"\\x{ord(m.group(0)):02x}",
        s,
    )


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
            parse_constant=reject_nonfinite,
        )


def load_json_text_or(path: str | Path, default: Any) -> Any:
    """``load_json_text`` that returns *default* when *path* is missing/malformed.

    Convenience helper for callers whose "missing file" behaviour is simply to
    substitute a fallback value (e.g. ``{}``).  Unreadable paths (e.g. a
    directory where a file is expected, ``IsADirectoryError``) also yield the
    default.
    """
    try:
        return load_json_text(path)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError,
            ValueError, OSError):
        return default


def atomic_write_text(path: str | Path, text: str, *,
                      encoding: str = "utf-8") -> None:
    """Atomically write *text* to *path*.

    The text is written to a unique sibling temp file (created with
    ``tempfile.mkstemp`` so symlinks are never followed, matching the
    ``O_NOFOLLOW`` defence used elsewhere in this repo) and moved into place
    with ``os.replace`` so readers never see a partial write.  The temp file
    is fsynced before the rename and removed on failure.
    """
    dest = Path(path)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent)
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, dest)
        tmp_path = None  # moved into place; nothing to clean up
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def atomic_write_json(path: str | Path, obj: Any) -> None:
    """Atomically write *obj* to *path* as JSON (``ensure_ascii=False, indent=2``).

    Same guarantees as :func:`atomic_write_text` (symlink-safe temp file,
    fsync, ``os.replace``).  Non-finite JSON constants are rejected
    (``allow_nan=False``) to match the reader-side guard in
    :func:`load_json_text`.
    """
    buffer = io.StringIO()
    _dump_json(buffer, obj)
    buffer.write("\n")
    atomic_write_text(path, buffer.getvalue())


def _dump_json(fh: TextIO, obj: Any) -> None:
    json.dump(obj, fh, ensure_ascii=False, indent=2, allow_nan=False)


def sha256_file(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file (chunked, memory-friendly)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute the SHA-256 hex digest of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def hash_line_range(
    file_path: str | Path,
    line_start: int,
    line_end: int,
) -> tuple[str, int]:
    """Compute SHA256 of lines [line_start, line_end] (1-indexed inclusive).

    Returns (hex_digest, actual_line_count).
    Normalizes text encoding to eliminate nondeterminism:
    - Reads as UTF-8 (strips BOM if present)
    - Treats CRLF and LF as equivalent (rstrip trailing newline chars)
    - Line-level trailing content (whitespace) is *preserved* — only
      the line-ending character (\\n, \\r\\n) is stripped for hashing.

    Raises ``FileNotFoundError`` / ``OSError`` if the file cannot be read;
    callers decide how to report a missing/unreadable file.
    """
    hasher = hashlib.sha256()
    line_count = 0
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for current_lineno, line in enumerate(f, start=1):
            if current_lineno > line_end:
                break
            if current_lineno >= line_start:
                # Strip trailing newline for deterministic hashing
                normalized = line.rstrip("\n\r")
                hasher.update(normalized.encode("utf-8"))
                line_count += 1
    return hasher.hexdigest(), line_count


def add_specback_dir_arg(parser: argparse.ArgumentParser, *,
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


def resolve_skill_path(specback_dir: str | Path, *,
                       rewrite: bool = False,
                       running_root: str | Path | None = None) -> Path:
    """Return a valid skill root for *specback_dir* (Issue #372 / SB-01).

    Phase 0 records the skill root's absolute path in ``{specback_dir}/.skill-path``.
    If the repo / skill / container moves, that stored path can become stale, and
    resume would try to invoke helper scripts from a missing location.  This helper
    validates the recorded path and, when invalid, falls back to the **currently
    running** skill root so a moved workspace resolves again without manual edits.

    Validation rules for a recorded path to be considered valid:
      - it is a non-empty string, and
      - it resolves to an existing directory, and
      - that directory contains a ``scripts/`` subdirectory (the marker every
        helper invocation relies on).

    When *rewrite* is True and the recorded path is invalid (or absent), the
    resolved fallback is written back to ``{specback_dir}/.skill-path`` so the
    fix is persisted, not just computed.
    """
    specialback = Path(specback_dir)
    if running_root is None:
        running_root = Path(__file__).resolve().parent.parent
    running = Path(running_root).resolve()

    marker = specialback / ".skill-path"
    recorded = ""
    if marker.exists():
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""

    candidate = (Path(recorded).resolve() if recorded
                 and Path(recorded).exists()
                 and (Path(recorded) / "scripts").is_dir() else running)

    if rewrite:
        new_value = str(candidate)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(marker, new_value + "\n")
        except OSError:
            pass
    return candidate
