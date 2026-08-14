#!/usr/bin/env python3
"""
specback build-inventory-from-sourcemap.py

Mechanically converts a source-map.json (v0.1.0 or v0.2.0) into an
inventory.json that Phase 2 can use directly — no manual grouping needed.

The mapping is 1:1 per unit:
  source-map unit → inventory item
  kind            → type        (or overridden via --role-to-type mapping)
  name            → name        (identical)
  path            → file        (identical)
  line_range[0]   → line        (start line)
  id              → related_source_ids[0]
  —               → covered_by  (empty list, filled by Phase 3)

Role → inventory-type mapping is built-in but overridable so different
languages/frameworks can fine-tune without editing the script.

Usage:
    python build-inventory-from-sourcemap.py \\
        --source-map .specback/source-map.json \\
        --output .specback/inventory.json

    # With a custom role → type mapping
    python build-inventory-from-sourcemap.py \\
        --source-map source-map.json \\
        --output inventory.json \\
        --role-to-type '{"endpoint":"api_endpoint","model":"orm_model"}'
"""

from __future__ import annotations

import argparse
import json
import sys
import artifact_io
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Default role → inventory type mapping
# Matches the taxonomy roles from source_map_v2/taxonomy.py.
# ---------------------------------------------------------------------------
DEFAULT_ROLE_TO_TYPE: dict[str, str] = {
    "module": "module",          # namespace / package
    "class": "class",            # class / trait / struct / record
    "model": "orm_model",        # persisted entity (ORM model)
    "schema": "schema",          # DTO / data type (Pydantic, TS interface)
    "component": "component",    # UI / view unit (React, Vue)
    "endpoint": "api_endpoint",  # HTTP/WS/GraphQL endpoint
    "route_group": "route_group",# a grouping of routes
    "callable": "function",      # function / method / procedure
    "command": "command",        # CLI / task entrypoint
    "job": "job",                # async / background worker
    "datastore": "datastore",    # individual DB object
    "migration": "migration",    # schema-change unit
    "dependency": "dependency",  # DI provider / middleware / hook
    "config": "config",          # config file / key
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_type(unit: dict[str, Any], role_to_type: dict[str, str]) -> str:
    """Determine the inventory ``type`` for a source-map unit.

    Priority:
      1. Direct lookup via ``role_to_type[unit.role]``
      2. Fallback to ``unit.kind`` (already framework-specific)
      3. Fallback to ``"unknown"``
    """
    role = unit.get("role", "")
    if role in role_to_type:
        return role_to_type[role]
    kind = unit.get("kind", "")
    if kind:
        return kind
    return "unknown"


def build_inventory(source_map: dict[str, Any], role_to_type: dict[str, str]) -> dict[str, Any]:
    """Convert a decoded source-map.json dict into an inventory.json dict.

    Handles both schema 0.2.0 (v2, role-typed, from source_map_v2) and
    schema 0.1.0 (v1, from source-map.py). For v1 units that have no ``role``
    field, the fallback uses ``unit.kind`` directly as the inventory type.
    """
    raw_units: list[dict[str, Any]] = source_map.get("units", [])
    inventory_units: list[dict[str, Any]] = []

    for i, unit in enumerate(raw_units):
        src_id = unit.get("id", f"SRC-{i + 1:04d}")
        inv_type = resolve_type(unit, role_to_type)

        item: dict[str, Any] = {
            "id": f"INV-{i + 1:04d}",
            "type": inv_type,
            "name": unit.get("name", ""),
            "file": unit.get("path", ""),
            "line": None,
            "covered_by": [],
            "related_source_ids": [src_id],
        }

        # Extract start line from line_range (optional, integer or [start, end])
        lr = unit.get("line_range")
        if isinstance(lr, list) and len(lr) >= 1:
            try:
                item["line"] = int(lr[0])
            except (ValueError, TypeError):
                pass
        elif isinstance(lr, int):
            item["line"] = lr

        inventory_units.append(item)

    return {
        "units": inventory_units,
    }


def load_source_map(path: Path) -> dict[str, Any]:
    """Load and validate a source-map.json file.

    Missing/invalid → ERROR + exit(2), unchanged.  The raw JSON read is
    delegated to :func:`artifact_io.load_source_map` (Issue #283).
    """
    if not path.exists():
        print(f"ERROR: source-map.json not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = artifact_io.load_source_map(path)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(2)
    if "units" not in data:
        print(f"ERROR: {path} has no 'units' key — is this a valid source-map.json?", file=sys.stderr)
        sys.exit(2)
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mechanically convert source-map.json → inventory.json",
    )
    p.add_argument(
        "--source-map", type=Path, default="specs/.specback/source-map.json",
        help="Path to source-map.json (default: .specback/source-map.json)",
    )
    p.add_argument(
        "--output", type=Path, default="specs/.specback/inventory.json",
        help="Path to output inventory.json (default: .specback/inventory.json)",
    )
    p.add_argument(
        "--role-to-type", type=str, default=None,
        help="JSON string overriding the default role → type mapping "
             '(e.g. \'{"endpoint":"api","model":"entity"}\')',
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Build the role → type mapping (merge CLI override into defaults)
    role_to_type = dict(DEFAULT_ROLE_TO_TYPE)
    if args.role_to_type:
        try:
            overrides = json.loads(args.role_to_type)
            if not isinstance(overrides, dict):
                print("ERROR: --role-to-type must be a JSON object", file=sys.stderr)
                return 2
            role_to_type.update(overrides)
        except json.JSONDecodeError as e:
            print(f"ERROR: --role-to-type is not valid JSON: {e}", file=sys.stderr)
            return 2

    source_map = load_source_map(args.source_map)
    inventory = build_inventory(source_map, role_to_type)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    count = len(inventory["units"])
    schema = source_map.get("schema_version", "?")
    print(
        f"build-inventory-from-sourcemap: {count} inventory items "
        f"from source-map schema {schema} → {args.output}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
