"""Tests for scripts/artifact_io.py — shared artifact loaders (Issue #283).

The consolidation's core guarantee is *output invariance*: each shared loader
must return exactly what the pre-#283 per-script implementation returned for
the same input.  The reference functions below are verbatim copies of the old
implementations (git HEAD 5e68b7b); every fixture is fed to both the
reference and the shared loader and the results are compared.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

# Ensure scripts/ is importable — artifact_io.py imports common.py.
SCRIPT = Path(__file__).resolve().parent.parent / "artifact_io.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

import artifact_io  # noqa: E402
from refutils import index_units_by_path  # noqa: E402


# ---------------------------------------------------------------------------
# Pre-#283 reference implementations (verbatim from git HEAD 5e68b7b)
# ---------------------------------------------------------------------------

def _ref_load_state(path: Path) -> dict | None:
    """detect-drift.py:126-133 / change-spec.py:372-379 (byte-identical)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _ref_detect_drift_load_source_map(path: Path) -> dict:
    """detect-drift.py:77-109 (indexed shape; missing file handled by caller)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    units = data.get("units", [])
    by_path: dict[str, list[dict]] = defaultdict(list)
    by_id: dict[str, dict] = {}
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


def _ref_fix_refs_load_source_map(specback_path: Path) -> dict[str, list[dict]]:
    """fix-refs.py:251-265 (by_path-only, sorted via index_units_by_path)."""
    sm_path = specback_path / "source-map.json"
    if not sm_path.exists():
        return {}
    try:
        data = json.loads(sm_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    units = data.get("units", []) if isinstance(data, dict) else data
    return index_units_by_path(units or [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_UNITS = [
    {"id": "SRC-0001", "path": "a.py", "line_range": [1, 10]},
    {"id": "SRC-0002", "path": "a.py", "line_range": [5, 20]},
    {"id": "SRC-0003", "path": "b.py", "line_range": [3, 8]},
    {"id": "SRC-0004", "line_range": [1, 2]},  # pathless unit (fix-refs skips)
]


def _write_source_map(dir_: Path, units: list[dict] | None = None) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / "source-map.json"
    p.write_text(json.dumps({
        "units": units if units is not None else SAMPLE_UNITS,
        "stats": {"files_scanned": 2},
        "target_root": "repo",
    }), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_state — output invariance vs detect-drift.py / change-spec.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [
    None,                      # missing file
    '{"generated_at_commit": "abc123"}',
    '{"phase": 3, "list": [1, 2]}',
    "{ not json",              # malformed JSON
    "",                        # empty file
    "[]",                      # valid JSON but not an object
])
def test_load_state_matches_reference(tmp_path: Path, content: str | None) -> None:
    p = tmp_path / "state.json"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    assert artifact_io.load_state(p) == _ref_load_state(p)


def test_load_state_unreadable_returns_none(tmp_path: Path) -> None:
    """A directory in place of state.json → OSError → None (old behaviour)."""
    d = tmp_path / "state.json"
    d.mkdir()
    assert artifact_io.load_state(d) is None
    assert _ref_load_state(d) is None


# ---------------------------------------------------------------------------
# load_source_map — output invariance vs detect-drift.py (indexed)
# ---------------------------------------------------------------------------

def test_load_source_map_indexed_matches_detect_drift_reference(tmp_path: Path) -> None:
    p = _write_source_map(tmp_path)
    assert artifact_io.load_source_map(p, build_indexes=True) == \
        _ref_detect_drift_load_source_map(p)


def test_load_source_map_indexed_shape(tmp_path: Path) -> None:
    p = _write_source_map(tmp_path)
    sm = artifact_io.load_source_map(p, build_indexes=True)
    assert set(sm.keys()) == {"units", "by_path", "by_id", "stats", "target_root"}
    assert sm["by_path"]["a.py"] == [
        {"id": "SRC-0001", "path": "a.py", "line_range": [1, 10]},
        {"id": "SRC-0002", "path": "a.py", "line_range": [5, 20]},
    ]
    assert sm["by_id"]["SRC-0003"]["path"] == "b.py"
    assert sm["stats"] == {"files_scanned": 2}
    assert sm["target_root"] == "repo"


def test_load_source_map_raw_matches_reference(tmp_path: Path) -> None:
    p = _write_source_map(tmp_path)
    assert artifact_io.load_source_map(p) == json.loads(p.read_text(encoding="utf-8"))


def test_load_source_map_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        artifact_io.load_source_map(tmp_path / "nope.json")


def test_load_source_map_malformed_raises(tmp_path: Path) -> None:
    p = tmp_path / "source-map.json"
    p.write_text("{ bad", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        artifact_io.load_source_map(p)


# ---------------------------------------------------------------------------
# load_source_map — output invariance vs fix-refs.py (by_path version)
# ---------------------------------------------------------------------------

def test_load_source_map_by_path_matches_fix_refs_reference(tmp_path: Path) -> None:
    sb = tmp_path / ".specback"
    _write_source_map(sb)
    new = index_units_by_path(
        artifact_io.load_source_map(sb / "source-map.json").get("units", []),
    )
    assert new == _ref_fix_refs_load_source_map(sb)


def test_load_source_map_by_path_missing_matches_fix_refs_reference(tmp_path: Path) -> None:
    sb = tmp_path / ".specback"
    sb.mkdir()
    assert _ref_fix_refs_load_source_map(sb) == {}
    # raw loader raises for the missing file; fix-refs' wrapper converts to {}
    with pytest.raises(FileNotFoundError):
        artifact_io.load_source_map(sb / "source-map.json")


def test_load_source_map_by_path_non_dict_units_matches_reference(tmp_path: Path) -> None:
    """fix-refs tolerates a top-level list payload; the shared raw loader must
    return it untouched so the wrapper can apply its own shape logic."""
    sb = tmp_path / ".specback"
    sb.mkdir()
    payload = [{"id": "SRC-0001", "path": "a.py", "line_range": [1, 2]}]
    (sb / "source-map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert artifact_io.load_source_map(sb / "source-map.json") == payload
    assert index_units_by_path(payload) == _ref_fix_refs_load_source_map(sb)


# ---------------------------------------------------------------------------
# load_trace
# ---------------------------------------------------------------------------

def test_load_trace_missing_returns_none(tmp_path: Path) -> None:
    assert artifact_io.load_trace(tmp_path / "trace.json") is None


def test_load_trace_parses(tmp_path: Path) -> None:
    p = tmp_path / "trace.json"
    p.write_text(json.dumps({"by_source": {"SRC-1": {"path": "a.py"}}}), encoding="utf-8")
    assert artifact_io.load_trace(p) == {"by_source": {"SRC-1": {"path": "a.py"}}}


def test_load_trace_malformed_propagates(tmp_path: Path) -> None:
    """coverage-check / detect-drift / change-spec all let JSONDecodeError
    propagate — the shared loader must too."""
    p = tmp_path / "trace.json"
    p.write_text("{ bad", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        artifact_io.load_trace(p)


# ---------------------------------------------------------------------------
# load_json_object — output invariance vs restore-sourcemap-from-trace.py
# ---------------------------------------------------------------------------

def test_load_json_object_parses_dict(tmp_path: Path) -> None:
    p = tmp_path / "trace.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert artifact_io.load_json_object(p) == {"a": 1}


def test_load_json_object_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        artifact_io.load_json_object(tmp_path / "nope.json")


def test_load_json_object_non_dict_raises(tmp_path: Path) -> None:
    p = tmp_path / "trace.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        artifact_io.load_json_object(p)


def test_load_json_object_malformed_raises(tmp_path: Path) -> None:
    p = tmp_path / "trace.json"
    p.write_text("{ bad", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        artifact_io.load_json_object(p)
