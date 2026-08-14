#!/usr/bin/env python3
"""
artifact_io.py — shared loaders for specback artifacts (source-map/trace/state).

Several scripts previously re-implemented the same artifact loaders with
slightly different shapes: loading source-map.json (with optional by_path /
by_id indexes), trace.json, and state.json.  This module is the single home
for those loaders (Issue #283).

Role split with common.py
-------------------------
* ``common.load_json_text`` is the *low-level* JSON reader: it decodes bytes
  and rejects non-finite constants, but knows nothing about specback
  artifacts.  It raises ``FileNotFoundError`` / ``json.JSONDecodeError`` /
  ``UnicodeDecodeError`` / ``ValueError``.
* This module is the *artifact-specific* layer: it knows where each artifact
  lives, what a missing file means for each one, and how to build the
  by_path / by_id indexes some callers need.

Missing-file policies (unchanged from the pre-#283 callers)
-----------------------------------------------------------
* ``load_state`` → ``None`` when missing or unreadable (detect-drift.py and
  change-spec.py behaved identically).
* ``load_trace`` → ``None`` when missing; callers decide what that means
  (coverage-check.py returns it as-is, detect-drift.py exits, change-spec.py
  substitutes ``{"by_source": {}}``).
* ``load_source_map`` → raises ``FileNotFoundError`` when missing; callers
  decide (build-trace.py re-raises, detect-drift.py exits, fix-refs.py
  returns ``{}``, change-spec.py substitutes an empty structure,
  build-inventory-from-sourcemap.py exits with a validation error).
* ``load_json_object`` → raises on missing / invalid / non-dict payload;
  restore-sourcemap-from-trace.py translates that into its existing
  ``ERROR`` + ``sys.exit(1)`` messages.

No loader in this module calls ``sys.exit`` — callers keep their own
user-facing error handling.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import load_json_text


def load_state(path: Path) -> dict[str, Any] | None:
    """Load state.json if it exists (silently return None otherwise).

    Matches the pre-#283 implementation shared byte-for-byte by
    detect-drift.py and change-spec.py: missing file, unreadable file, and
    malformed JSON all yield ``None``.
    """
    if not path.exists():
        return None
    try:
        return load_json_text(path)
    except (json.JSONDecodeError, OSError):
        return None


def load_trace(path: Path) -> dict[str, Any] | None:
    """Load trace.json, or ``None`` when the file is missing.

    Malformed JSON still propagates ``json.JSONDecodeError`` (the pre-#283
    callers all let it propagate); only a *missing* file yields ``None`` so
    each caller can apply its own policy.
    """
    if not path.exists():
        return None
    return load_json_text(path)


def load_source_map(path: Path, *, build_indexes: bool = False) -> dict[str, Any]:
    """Load source-map.json; raises ``FileNotFoundError`` when missing.

    With ``build_indexes=True`` the returned dict has the detect-drift.py /
    change-spec.py shape: ``{"units", "by_path", "by_id", "stats",
    "target_root"}`` where ``by_path`` maps path → list of unit dicts
    (insertion order, including units without a path under the ``""`` key)
    and ``by_id`` maps SRC-ID → unit dict.

    With ``build_indexes=False`` (default) the raw parsed dict is returned —
    the shape build-trace.py, fix-refs.py and build-inventory-from-sourcemap.py
    consume.
    """
    data = load_json_text(path)
    if not build_indexes:
        return data
    units = data.get("units", [])
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[str, dict[str, Any]] = {}
    for unit in units:
        uid = unit.get("id", "")
        u_path = unit.get("path", "")
        by_path[u_path].append(unit)
        by_id[uid] = unit
    return {
        "units": units,
        "by_path": dict(by_path),
        "by_id": by_id,
        "stats": data.get("stats", {}),
        "target_root": data.get("target_root", ""),
    }


def load_json_object(path: Path) -> dict[str, Any]:
    """Load *path* as a JSON object (dict), raising on any failure.

    Raises ``FileNotFoundError`` when missing, ``json.JSONDecodeError`` /
    ``UnicodeDecodeError`` / ``OSError`` for unreadable content, and
    ``ValueError`` when the decoded value is not a dict.  This is the
    validated-object read restore-sourcemap-from-trace.py used to implement
    inline as ``_load_json_object`` (Issue #283).
    """
    data = load_json_text(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data
