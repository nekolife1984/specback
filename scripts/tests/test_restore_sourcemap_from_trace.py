"""Tests for restore-sourcemap-from-trace.py.

Verifies that a fully re-generated source-map.json (with renumbered SRC-IDs)
can be restored to the old ID layout with new units appended at the end,
without touching the old units' IDs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "restore-sourcemap-from-trace.py"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Minimal spec project: committed old trace.json + re-generated source-map.json."""
    r = tmp_path / "repo"
    (r / "specs/.specback").mkdir(parents=True)

    # Old committed trace.json — the source of truth for old unit IDs.
    old_trace = {
        "by_source": {
            "SRC-0001": {
                "path": "app.py",
                "line_range": [1, 10],
                "kind": "py_function",
                "name": "main",
            },
            "SRC-0002": {
                "path": "src/mod.py",
                "line_range": [5, 20],
                "kind": "py_function",
                "name": "helper",
            },
        }
    }
    (r / "specs/trace.json").write_text(json.dumps(old_trace), encoding="utf-8")

    # Re-generated source-map.json — old units renumbered + new module units.
    new_map = {
        "schema_version": "0.1.0",
        "units": [
            {
                "id": "SRC-0001",
                "path": "src/mod.py",
                "line_range": [5, 20],
                "kind": "py_function",
                "name": "helper",
            },
            {
                "id": "SRC-0002",
                "path": "app.py",
                "line_range": [1, 10],
                "kind": "py_function",
                "name": "main",
            },
            {
                "id": "SRC-0003",
                "path": "src/new_module.py",
                "line_range": [1, 12],
                "kind": "py_function",
                "name": "new_fn",
            },
            {
                "id": "SRC-0004",
                "path": "src/new_module.py",
                "line_range": [13, 25],
                "kind": "py_function",
                "name": "another_fn",
            },
        ],
    }
    (r / "specs/.specback/source-map.json").write_text(
        json.dumps(new_map), encoding="utf-8"
    )
    return r


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _load_units(repo: Path) -> list[dict]:
    data = json.loads(
        (repo / "specs/.specback/source-map.json").read_text(encoding="utf-8")
    )
    return data["units"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_restores_old_ids_and_appends_new(repo: Path) -> None:
    """Old unit IDs are preserved; new units are appended from old_max+1."""
    result = _run(repo, "--new-ids", "SRC-0003,SRC-0004")
    assert result.returncode == 0, result.stderr

    units = _load_units(repo)
    by_id = {u["id"]: u for u in units}

    # Old units keep their original IDs and metadata
    assert by_id["SRC-0001"]["name"] == "main"
    assert by_id["SRC-0001"]["path"] == "app.py"
    assert by_id["SRC-0002"]["name"] == "helper"
    assert by_id["SRC-0002"]["path"] == "src/mod.py"

    # New units are renumbered from old_max (2) + 1
    assert by_id["SRC-0003"]["name"] == "new_fn"
    assert by_id["SRC-0003"]["path"] == "src/new_module.py"
    assert by_id["SRC-0004"]["name"] == "another_fn"

    # Order: old units first, then new units
    assert [u["id"] for u in units] == ["SRC-0001", "SRC-0002", "SRC-0003", "SRC-0004"]


def test_no_new_ids_keeps_old_units(repo: Path) -> None:
    """Without --new-ids, the map is just the old units (no additions)."""
    result = _run(repo)
    assert result.returncode == 0, result.stderr

    units = _load_units(repo)
    assert [u["id"] for u in units] == ["SRC-0001", "SRC-0002"]
    assert units[0]["name"] == "main"


def test_rejects_missing_repo(tmp_path: Path) -> None:
    """A missing repo directory fails cleanly."""
    result = _run(tmp_path / "missing")
    assert result.returncode != 0


def test_inventory_regeneration_hint_in_output(repo: Path) -> None:
    """Output reports the restored unit counts (used for manual verification)."""
    result = _run(repo, "--new-ids", "SRC-0003")
    assert result.returncode == 0
    assert "2 old units + 1 new = 3 units" in result.stdout
