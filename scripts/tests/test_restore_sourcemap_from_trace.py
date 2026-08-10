"""Tests for restore-sourcemap-from-trace.py.

Verifies that a fully re-generated source-map.json (with renumbered SRC-IDs)
can be restored to the old ID layout with new units appended at the end,
without touching the old units' IDs. Also verifies the safety guards added
in Issue #247: idempotency, --new-ids validation, backup + atomic write,
and clean error handling.
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
# Happy path
# ---------------------------------------------------------------------------


def test_restores_old_ids_and_appends_new(repo: Path) -> None:
    """Old unit IDs are preserved; new units are appended from old_max+1."""
    result = _run(repo, "--new-ids", "SRC-0003,SRC-0004", "--apply")
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
    result = _run(repo, "--apply")
    assert result.returncode == 0, result.stderr

    units = _load_units(repo)
    assert [u["id"] for u in units] == ["SRC-0001", "SRC-0002"]
    assert units[0]["name"] == "main"


def test_inventory_regeneration_hint_in_output(repo: Path) -> None:
    """Output reports the restored unit counts (used for manual verification)."""
    result = _run(repo, "--new-ids", "SRC-0003")
    assert result.returncode == 0
    assert "2 old units + 1 new = 3 units" in result.stdout


# ---------------------------------------------------------------------------
# Dry-run / apply / backup
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(repo: Path) -> None:
    """Without --apply the restored map is reported but the file is untouched."""
    before = (repo / "specs/.specback/source-map.json").read_text(encoding="utf-8")
    result = _run(repo, "--new-ids", "SRC-0003,SRC-0004")
    assert result.returncode == 0, result.stderr
    assert "Dry-run" in result.stdout
    assert (repo / "specs/.specback/source-map.json").read_text(
        encoding="utf-8"
    ) == before
    assert not (repo / "specs/.specback/source-map.json.pre-restore").exists()


def test_apply_creates_backup_before_overwrite(repo: Path) -> None:
    """--apply saves source-map.json.pre-restore with the original content."""
    original = (repo / "specs/.specback/source-map.json").read_text(encoding="utf-8")
    result = _run(repo, "--new-ids", "SRC-0003", "--apply")
    assert result.returncode == 0, result.stderr

    backup = repo / "specs/.specback/source-map.json.pre-restore"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original


def test_apply_marks_map_as_restored(repo: Path) -> None:
    """The written map carries restored_from: trace.json for the idempotency guard."""
    result = _run(repo, "--new-ids", "SRC-0003", "--apply")
    assert result.returncode == 0, result.stderr
    data = json.loads(
        (repo / "specs/.specback/source-map.json").read_text(encoding="utf-8")
    )
    assert data["restored_from"] == "trace.json"


def test_rerun_without_force_is_refused(repo: Path) -> None:
    """A second run on the already-restored map is refused (identity swap risk)."""
    first = _run(repo, "--new-ids", "SRC-0003,SRC-0004", "--apply")
    assert first.returncode == 0, first.stderr

    second = _run(repo, "--new-ids", "SRC-0003,SRC-0004", "--apply")
    assert second.returncode == 1
    assert "already restored" in second.stderr
    # The map is untouched by the refused run.
    units = _load_units(repo)
    assert [u["id"] for u in units] == ["SRC-0001", "SRC-0002", "SRC-0003", "SRC-0004"]


def test_rerun_with_force_allowed(repo: Path) -> None:
    """--force bypasses the restored-guard; a plain old-unit restore then works."""
    first = _run(repo, "--new-ids", "SRC-0003,SRC-0004", "--apply")
    assert first.returncode == 0, first.stderr

    second = _run(repo, "--force", "--apply")
    assert second.returncode == 0, second.stderr
    units = _load_units(repo)
    assert [u["id"] for u in units] == ["SRC-0001", "SRC-0002"]


# ---------------------------------------------------------------------------
# --new-ids validation
# ---------------------------------------------------------------------------


def test_rejects_id_not_in_regenerated_map(repo: Path) -> None:
    """An ID that does not exist in the re-generated map is a hard error."""
    result = _run(repo, "--new-ids", "SRC-0099", "--apply")
    assert result.returncode == 1
    assert "not present in the re-generated map" in result.stderr
    # Nothing was written.
    assert not (repo / "specs/.specback/source-map.json.pre-restore").exists()


def test_rejects_old_unit_id(repo: Path) -> None:
    """Passing an old unit ID would duplicate units — must error."""
    result = _run(repo, "--new-ids", "SRC-0001", "--apply")
    assert result.returncode == 1
    assert "old unit IDs" in result.stderr


def test_rejects_malformed_id(repo: Path) -> None:
    """Non SRC-NNNN values are rejected up front."""
    result = _run(repo, "--new-ids", "app.py,SRC-0003", "--apply")
    assert result.returncode == 1
    assert "malformed ID" in result.stderr


def test_partial_new_ids_missing_is_error(repo: Path) -> None:
    """Asking for 2 IDs but only 1 resolvable is an error, not silent data loss."""
    result = _run(repo, "--new-ids", "SRC-0003,SRC-0099", "--apply")
    assert result.returncode == 1
    assert "not present in the re-generated map" in result.stderr


# ---------------------------------------------------------------------------
# Error handling (clean failures, no tracebacks)
# ---------------------------------------------------------------------------


def test_rejects_missing_repo(tmp_path: Path) -> None:
    """A missing repo directory fails cleanly."""
    result = _run(tmp_path / "missing")
    assert result.returncode != 0
    assert "trace.json not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_rejects_missing_trace(repo: Path) -> None:
    """Missing specs/trace.json fails with a clean error."""
    (repo / "specs/trace.json").unlink()
    result = _run(repo)
    assert result.returncode == 1
    assert "trace.json not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_rejects_missing_source_map(repo: Path) -> None:
    """Missing source-map.json fails with a clean error."""
    (repo / "specs/.specback/source-map.json").unlink()
    result = _run(repo)
    assert result.returncode == 1
    assert "source-map.json not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_rejects_empty_by_source(repo: Path) -> None:
    """Empty by_source fails with a clean error instead of max() on empty seq."""
    (repo / "specs/trace.json").write_text(
        json.dumps({"by_source": {}}), encoding="utf-8"
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "no by_source entries" in result.stderr
    assert "Traceback" not in result.stderr


def test_rejects_missing_by_source_key(repo: Path) -> None:
    """trace.json without by_source key fails cleanly (no KeyError)."""
    (repo / "specs/trace.json").write_text(json.dumps({}), encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 1
    assert "no by_source entries" in result.stderr
    assert "Traceback" not in result.stderr


def test_rejects_invalid_json(repo: Path) -> None:
    """Corrupt JSON fails with a clean error (no JSONDecodeError traceback)."""
    (repo / "specs/trace.json").write_text("{ not json", encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 1
    assert "cannot read trace.json" in result.stderr
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# Metadata preservation
# ---------------------------------------------------------------------------


def test_preserves_fingerprint_when_present(repo: Path) -> None:
    """fingerprint/signature from trace.json are carried into the restored map."""
    trace_path = repo / "specs/trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["by_source"]["SRC-0001"]["fingerprint"] = "sha1:abc123"
    trace["by_source"]["SRC-0001"]["signature"] = "def main():"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")

    result = _run(repo, "--new-ids", "SRC-0003", "--apply")
    assert result.returncode == 0, result.stderr

    by_id = {u["id"]: u for u in _load_units(repo)}
    assert by_id["SRC-0001"]["fingerprint"] == "sha1:abc123"
    assert by_id["SRC-0001"]["signature"] == "def main():"


def test_warns_on_missing_fingerprint(repo: Path) -> None:
    """Restoring without fingerprint warns on stderr (drift detection degraded)."""
    result = _run(repo, "--new-ids", "SRC-0003", "--apply")
    assert result.returncode == 0, result.stderr
    assert "without fingerprint" in result.stderr
