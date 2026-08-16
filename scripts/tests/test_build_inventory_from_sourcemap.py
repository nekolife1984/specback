"""Tests for build-inventory-from-sourcemap.py.

Tests both the CLI interface and the conversion logic using temp files.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "build-inventory-from-sourcemap.py"

# Ensure scripts/ is importable — build-inventory-from-sourcemap.py shares
# artifact loaders with scripts/artifact_io.py (Issue #283).
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

# Load the script module once via importlib so the pure conversion functions can
# be exercised directly (Issue #325) rather than only through the subprocess CLI.
import importlib.util

_inv_spec = importlib.util.spec_from_file_location("build_inventory_src", SCRIPT)
assert _inv_spec is not None and _inv_spec.loader is not None
_inv_mod = importlib.util.module_from_spec(_inv_spec)
_inv_spec.loader.exec_module(_inv_mod)

resolve_type = _inv_mod.resolve_type
build_inventory = _inv_mod.build_inventory
DEFAULT_ROLE_TO_TYPE = _inv_mod.DEFAULT_ROLE_TO_TYPE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

V2_SOURCE_MAP = {
    "schema_version": "0.2.0",
    "target_root": "myapp",
    "generated_at": "2026-07-29T21:00:00",
    "detected_frameworks": [],
    "warnings": [],
    "stats": {"files_scanned": 3, "files_excluded": 0, "units_total": 4},
    "units": [
        {
            "id": "SRC-0001", "path": "app/main.py", "line_range": [10, 30],
            "language": "python", "role": "endpoint", "table": "Actions",
            "kind": "fastapi_endpoint", "tier": "middle", "name": "create_item",
            "signature": "async def create_item(item: Item)",
            "fingerprint": "sha1:abc123",
            "endpoint": {"method": "POST", "path": "/items"},
        },
        {
            "id": "SRC-0002", "path": "app/schemas.py", "line_range": [5, 15],
            "language": "python", "role": "schema", "table": "Entities",
            "kind": "pydantic_schema", "tier": "middle", "name": "Item",
            "signature": "class Item(BaseModel)",
            "fingerprint": "sha1:def456",
        },
        {
            "id": "SRC-0003", "path": "app/models.py", "line_range": [1, 50],
            "language": "python", "role": "model", "table": "Entities",
            "kind": "django_model", "tier": "middle", "name": "Product",
            "signature": "class Product(models.Model)",
            "fingerprint": "sha1:ghi789",
        },
        {
            "id": "SRC-0004", "path": "app/migrations/0001_initial.py",
            "line_range": [1, 100], "language": "python", "role": "migration",
            "table": "Data", "kind": "django_migration", "tier": "macro",
            "name": "0001_initial",
            "signature": "class Migration(migrations.Migration)",
            "fingerprint": "sha1:jkl012",
        },
    ],
}

V1_SOURCE_MAP = {
    "schema_version": "0.1.0",
    "target_root": "myapp",
    "generated_at": "2026-07-29T21:00:00",
    "stats": {"files_scanned": 2, "files_excluded": 0, "units_total": 3},
    "units": [
        {
            "id": "SRC-0010", "path": "app/models.py", "line_range": [1, 30],
            "kind": "py_class", "name": "Item",
            "signature": "class Item:", "fingerprint": "sha1:abc",
        },
        {
            "id": "SRC-0011", "path": "app/main.py", "line_range": [5, 10],
            "kind": "py_function", "name": "do_stuff",
            "signature": "def do_stuff():", "fingerprint": "sha1:def",
        },
        {
            "id": "SRC-0012", "path": "config/routes.rb", "line_range": [1, 3],
            "kind": "rails_route", "name": "resources:issues",
            "signature": "resources :issues", "fingerprint": "sha1:ghi",
        },
    ],
}


def run_script(tmp_path: Path, args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run build-inventory-from-sourcemap.py with given args, return CompletedProcess."""
    cmd = [sys.executable, str(SCRIPT)]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def write_source_map(tmp_path: Path, data: dict, name: str = "source-map.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_inventory(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI interface tests
# ---------------------------------------------------------------------------


def test_help():
    result = run_script(None, ["--help"])
    assert result.returncode == 0
    assert "--source-map" in result.stdout
    assert "--output" in result.stdout
    assert "--role-to-type" in result.stdout


def test_missing_source_map(tmp_path: Path):
    result = run_script(tmp_path, ["--source-map", str(tmp_path / "nope.json")])
    assert result.returncode == 2
    assert "not found" in result.stderr


def test_invalid_json(tmp_path: Path):
    sm = tmp_path / "bad.json"
    sm.write_text("not json", encoding="utf-8")
    result = run_script(tmp_path, ["--source-map", str(sm)])
    assert result.returncode == 2
    assert "invalid JSON" in result.stderr


def test_nonfinite_json(tmp_path: Path):
    """NaN in source-map.json → clean exit 2 (shared loader rejects non-finite)."""
    sm = tmp_path / "nan.json"
    sm.write_text(
        '{"units": [{"id": "SRC-1", "line_range": [NaN, 5]}]}',
        encoding="utf-8",
    )
    result = run_script(tmp_path, ["--source-map", str(sm)])
    assert result.returncode == 2
    assert "invalid JSON" in result.stderr


def test_no_units_key(tmp_path: Path):
    sm = tmp_path / "no-units.json"
    sm.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    result = run_script(tmp_path, ["--source-map", str(sm)])
    assert result.returncode == 2
    assert "no 'units' key" in result.stderr


def test_invalid_role_to_type(tmp_path: Path):
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    result = run_script(tmp_path, [
        "--source-map", str(sm),
        "--role-to-type", "not-json",
    ])
    assert result.returncode == 2
    assert "not valid JSON" in result.stderr


def test_load_source_map_matches_artifact_io(tmp_path: Path):
    """load_source_map returns the raw dict artifact_io parses (Issue #283)."""
    import artifact_io

    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    assert _inv_mod.load_source_map(sm) == artifact_io.load_source_map(sm)


# ---------------------------------------------------------------------------
# Functional conversion tests (v2 schema — with role field)
# ---------------------------------------------------------------------------


def test_v2_conversion(tmp_path: Path):
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    result = run_script(tmp_path, [
        "--source-map", str(sm),
        "--output", str(out),
    ])
    assert result.returncode == 0
    assert out.exists()

    inv = load_inventory(out)
    assert len(inv["units"]) == 4

    # Check role → type mapping
    types = {u["id"]: u["type"] for u in inv["units"]}
    assert types["INV-0001"] == "api_endpoint"  # endpoint
    assert types["INV-0002"] == "schema"         # schema
    assert types["INV-0003"] == "orm_model"      # model
    assert types["INV-0004"] == "migration"      # migration


def test_v2_related_source_ids(tmp_path: Path):
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    run_script(tmp_path, ["--source-map", str(sm), "--output", str(out)])
    inv = load_inventory(out)

    for item in inv["units"]:
        assert len(item["related_source_ids"]) == 1
        src_id = item["related_source_ids"][0]
        assert src_id.startswith("SRC-")


def test_v2_fields_preserved(tmp_path: Path):
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    run_script(tmp_path, ["--source-map", str(sm), "--output", str(out)])
    inv = load_inventory(out)

    # First item
    item = inv["units"][0]
    assert item["name"] == "create_item"
    assert item["file"] == "app/main.py"
    assert item["line"] == 10
    assert item["covered_by"] == []


def test_v2_covered_by_empty(tmp_path: Path):
    """covered_by is always initialised to empty list (filled by Phase 3)."""
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    run_script(tmp_path, ["--source-map", str(sm), "--output", str(out)])
    inv = load_inventory(out)
    for item in inv["units"]:
        assert item["covered_by"] == []


def test_v2_role_to_type_override(tmp_path: Path):
    sm = write_source_map(tmp_path, V2_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    result = run_script(tmp_path, [
        "--source-map", str(sm),
        "--output", str(out),
        "--role-to-type", '{"endpoint":"api","model":"entity"}',
    ])
    assert result.returncode == 0

    inv = load_inventory(out)
    types = {u["id"]: u["type"] for u in inv["units"]}
    assert types["INV-0001"] == "api"       # overridden
    assert types["INV-0002"] == "schema"    # default (not overridden)
    assert types["INV-0003"] == "entity"    # overridden
    assert types["INV-0004"] == "migration" # default


# ---------------------------------------------------------------------------
# Functional conversion tests (v1 schema — no role field, fallback to kind)
# ---------------------------------------------------------------------------


def test_v1_fallback_to_kind(tmp_path: Path):
    sm = write_source_map(tmp_path, V1_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    result = run_script(tmp_path, [
        "--source-map", str(sm),
        "--output", str(out),
    ])
    assert result.returncode == 0

    inv = load_inventory(out)
    assert len(inv["units"]) == 3

    types = {u["id"]: u["type"] for u in inv["units"]}
    assert types["INV-0001"] == "py_class"      # fallback to kind
    assert types["INV-0002"] == "py_function"   # fallback to kind
    assert types["INV-0003"] == "rails_route"   # fallback to kind


def test_v1_related_source_ids(tmp_path: Path):
    sm = write_source_map(tmp_path, V1_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    run_script(tmp_path, ["--source-map", str(sm), "--output", str(out)])
    inv = load_inventory(out)

    # SRC-0010 → INV-0001, SRC-0011 → INV-0002, SRC-0012 → INV-0003
    expected = {"INV-0001": "SRC-0010", "INV-0002": "SRC-0011", "INV-0003": "SRC-0012"}
    for item in inv["units"]:
        assert item["related_source_ids"] == [expected[item["id"]]]


def test_v1_line_range_start(tmp_path: Path):
    """line from line_range[0]."""
    sm = write_source_map(tmp_path, V1_SOURCE_MAP)
    out = tmp_path / "inventory.json"
    run_script(tmp_path, ["--source-map", str(sm), "--output", str(out)])
    inv = load_inventory(out)

    lines = {u["id"]: u["line"] for u in inv["units"]}
    assert lines["INV-0001"] == 1   # line_range [1, 30]
    assert lines["INV-0002"] == 5   # line_range [5, 10]
    assert lines["INV-0003"] == 1   # line_range [1, 3]


# ---------------------------------------------------------------------------
# Direct function-level tests (Issue #325 — importlib, not subprocess)
# ---------------------------------------------------------------------------


class TestResolveTypeDirect:
    def test_role_lookup_priority(self):
        unit = {"role": "endpoint", "kind": "fastapi_endpoint"}
        assert resolve_type(unit, {"endpoint": "api_endpoint"}) == "api_endpoint"

    def test_missing_role_falls_back_to_kind(self):
        unit = {"kind": "rails_route"}
        assert resolve_type(unit, DEFAULT_ROLE_TO_TYPE) == "rails_route"

    def test_empty_role_falls_back_to_kind(self):
        unit = {"role": "", "kind": "py_class"}
        assert resolve_type(unit, {}) == "py_class"

    def test_unknown_returns_unknown(self):
        unit = {"role": "mystery", "kind": ""}
        assert resolve_type(unit, {}) == "unknown"

    def test_role_not_in_mapping_falls_back_to_kind(self):
        unit = {"role": "custom", "kind": "custom_kind"}
        assert resolve_type(unit, {}) == "custom_kind"


class TestBuildInventoryDirect:
    def test_empty_units(self):
        out = build_inventory({"units": []}, DEFAULT_ROLE_TO_TYPE)
        assert out == {"units": []}

    def test_item_shape(self):
        out = build_inventory(V2_SOURCE_MAP, DEFAULT_ROLE_TO_TYPE)
        items = out["units"]
        assert len(items) == 4
        item = items[0]
        assert item["id"] == "INV-0001"
        assert item["type"] == "api_endpoint"
        assert item["name"] == "create_item"
        assert item["file"] == "app/main.py"
        assert item["line"] == 10
        assert item["covered_by"] == []
        assert item["related_source_ids"] == ["SRC-0001"]

    def test_role_to_type_override(self):
        # A partial override dict leaves other roles to fall back to their kind.
        out = build_inventory(
            V2_SOURCE_MAP, {"endpoint": "api", "model": "entity"},
        )
        types = {u["id"]: u["type"] for u in out["units"]}
        assert types["INV-0001"] == "api"
        assert types["INV-0002"] == "pydantic_schema"  # not overridden → kind
        assert types["INV-0003"] == "entity"
        assert types["INV-0004"] == "django_migration"  # not overridden → kind

    def test_type_falls_back_to_kind(self):
        out = build_inventory(V1_SOURCE_MAP, DEFAULT_ROLE_TO_TYPE)
        types = {u["id"]: u["type"] for u in out["units"]}
        assert types["INV-0001"] == "py_class"
        assert types["INV-0002"] == "py_function"
        assert types["INV-0003"] == "rails_route"

    def test_line_parsing_int_like_list(self):
        sm = {"units": [
            {"id": "SRC-1", "path": "a.py", "line_range": [10, 20], "kind": ""},
            {"id": "SRC-2", "path": "b.py", "line_range": 7, "kind": ""},
            {"id": "SRC-3", "path": "c.py", "line_range": "bad", "kind": ""},
        ]}
        out = build_inventory(sm, {})
        assert [u["line"] for u in out["units"]] == [10, 7, None]

    def test_missing_src_id_generated(self):
        sm = {"units": [{"path": "a.py", "kind": ""}]}
        item = build_inventory(sm, {})["units"][0]
        assert item["related_source_ids"] == ["SRC-0001"]
