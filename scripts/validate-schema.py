#!/usr/bin/env python3
"""
validate-schema.py — JSON Schema validation for specback config files.

Validates a data file (goal.json, state.json, questions.json, etc.)
against its corresponding JSON Schema file and reports any violations.

Exit codes:
  0  — validation passed
  1  — validation failed (schema violations found)
  2  — usage error (missing arg, file not found, invalid JSON, etc.)

Usage:
    python3 validate-schema.py --schema <schema.json> --data-file <data.json>
    python3 validate-schema.py --schema schemas/goal.schema.json \\
        --data-file .specback/goal.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Minimal inline JSON Schema validator (no external dependencies)
#
# Supports: draft-07 subset — type, enum, required, properties,
# patternProperties, additionalProperties, items, minItems, uniqueItems,
# minLength, min/maximum, pattern, format (date-time only), $ref (resolved
# from a bundled defs map), default, description (ignored).
# ---------------------------------------------------------------------------

def _resolve_ref(ref: str, defs: dict) -> dict:
    """Resolve a JSON Pointer $ref against the definitions map."""
    if not ref.startswith("#/$defs/"):
        raise NotImplementedError(f"Only #/$defs/ refs supported, got: {ref}")
    key = ref[len("#/$defs/"):]
    if key not in defs:
        raise ValueError(f"Unresolved $ref: {ref}")
    return defs[key]


def _validate_value(value, schema: dict, path: str, defs: dict) -> list[str]:
    """Validate a single value against a schema fragment. Returns error list."""
    errors: list[str] = []

    # nullable via top-level type array containing "null"
    types = schema.get("type")
    type_list = [types] if isinstance(types, str) else (types or [])

    if "$ref" in schema:
        ref_schema = _resolve_ref(schema["$ref"], defs)
        return _validate_value(value, ref_schema, path, defs)

    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(
                f"{path}: value {json.dumps(value, ensure_ascii=False)} "
                f"is not one of {json.dumps(schema['enum'], ensure_ascii=False)}"
            )
        return errors  # enum takes precedence over type

    # Type check (skip None when nullable)
    if type_list and value is not None:
        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str, "integer": int, "number": (int, float),
            "boolean": bool, "array": list, "object": dict,
        }
        for t_name in type_list:
            py_type = type_map.get(t_name)
            if py_type and not isinstance(value, py_type):
                errors.append(
                    f"{path}: expected {t_name}, got {type(value).__name__}"
                )
                break

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string too short ({len(value)} < {schema['minLength']})")
        if "pattern" in schema:
            import re
            if not re.match(schema["pattern"], value):
                errors.append(f"{path}: does not match pattern {schema['pattern']}")
        if schema.get("format") == "date-time":
            from datetime import datetime
            try:
                datetime.fromisoformat(value)
            except (ValueError, TypeError):
                errors.append(f"{path}: invalid date-time format: {value!r}")

    elif isinstance(value, int) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum ({schema['minimum']})")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum ({schema['maximum']})")

    elif isinstance(value, dict):
        _validate_object(value, schema, path, defs, errors)

    elif isinstance(value, list):
        _validate_array(value, schema, path, defs, errors)

    return errors


def _validate_object(obj: dict, schema: dict, path: str, defs: dict,
                     errors: list[str]) -> None:
    """Validate an object value against properties / required / patternProperties."""
    props = schema.get("properties", {})
    required = schema.get("required", [])
    pattern_props = schema.get("patternProperties", {})
    addl = schema.get("additionalProperties", True)

    # Check required
    for r in required:
        if r not in obj:
            errors.append(f"{path}: missing required property {r!r}")

    # Check each property
    for key, val in obj.items():
        sub = f"{path}.{key}"
        if key in props:
            errors.extend(_validate_value(val, props[key], sub, defs))
        elif pattern_props:
            import re
            matched = False
            for pat, pat_schema in pattern_props.items():
                if re.match(pat, key):
                    errors.extend(_validate_value(val, pat_schema, sub, defs))
                    matched = True
                    break
            if not matched and not addl:
                errors.append(f"{sub}: unexpected property")
        elif not addl:
            errors.append(f"{sub}: unexpected property")


def _validate_array(arr: list, schema: dict, path: str, defs: dict,
                    errors: list[str]) -> None:
    """Validate an array value against items / minItems / uniqueItems."""
    items_schema = schema.get("items", {})
    if items_schema:
        for i, item in enumerate(arr):
            sub = f"{path}[{i}]"
            errors.extend(_validate_value(item, items_schema, sub, defs))

    if "minItems" in schema and len(arr) < schema["minItems"]:
        errors.append(f"{path}: too few items ({len(arr)} < {schema['minItems']})")

    if schema.get("uniqueItems") and len(arr) != len(set(arr)):
        errors.append(f"{path}: duplicate items found")


def validate(data, schema: dict, label: str = "root") -> list[str]:
    """Validate Python data against a parsed schema. Returns error list."""
    defs = schema.get("$defs", {})
    return _validate_value(data, schema, label, defs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a JSON data file against a JSON Schema."
    )
    parser.add_argument(
        "--schema", "-s",
        required=True,
        help="Path to the JSON Schema file",
    )
    parser.add_argument(
        "--data-file", "-d",
        required=True,
        help="Path to the JSON data file to validate",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed validation output",
    )
    args = parser.parse_args(argv)

    # Resolve paths relative to script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(script_dir, args.schema) if not os.path.isabs(args.schema) else args.schema

    if not os.path.exists(schema_path):
        print(f"ERROR: Schema file not found: {schema_path}", file=sys.stderr)
        return 2
    if not os.path.exists(args.data_file):
        print(f"ERROR: Data file not found: {args.data_file}", file=sys.stderr)
        return 2

    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid schema JSON: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.data_file, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid data JSON: {e}", file=sys.stderr)
        return 2

    errors = validate(data, schema)

    if errors:
        print(f"❌ Validation FAILED — {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"   • {err}", file=sys.stderr)
        return 1
    else:
        if args.verbose:
            print(f"✅ Validation passed: {os.path.basename(args.data_file)} "
                  f"✓ {os.path.basename(schema_path)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
