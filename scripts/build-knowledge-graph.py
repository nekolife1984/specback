#!/usr/bin/env python3
"""
specback build-knowledge-graph.py

Converts source-map.json, trace.json, and inventory.json into a
JSON-LD Knowledge Graph (knowledge-graph.jsonld) that external tools
(GraphDB / Neo4j / GBrain / Obsidian) can query via SPARQL or Cypher.

Output schema (JSON-LD with @context):

  SourceUnit nodes — every unit from source-map.json
    @id: ccrsg:unit/SRC-NNNN
    type: ccrsg:SourceUnit
    properties: sourceId, role, kind, path, lineRange, tier, language, name,
                framework, fingerprint

  InventoryItem nodes — every item from inventory.json
    @id: ccrsg:inv/INV-NNNN
    type: ccrsg:InventoryItem
    properties: inventoryId, type, name, sourceId
    relation: ccrsg:derivedFrom → ccrsg:unit/SRC-NNNN

  SpecChapter nodes — aggregated from trace.json by_section
    @id: ccrsg:chapter/<slug>
    type: ccrsg:SpecChapter
    properties: chapterNumber, title
    relation: ccrsg:coversUnit → [ccrsg:unit/SRC-NNNN, ...]

  Question nodes — from questions.json
    @id: ccrsg:question/Q-NNN
    type: ccrsg:Question
    properties: category, severity, status, text
    relation: ccrsg:raisesForUnit → ccrsg:unit/SRC-NNNN

Usage:
    python build-knowledge-graph.py \
        --source-map .specback/source-map.json \
        --trace .specback/trace.json \
        --inventory .specback/inventory.json \
        --output .specback/knowledge-graph.jsonld

    # Skip if not needed (e.g. in CI when questions.json is absent)
    python build-knowledge-graph.py ... --skip-questions
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from common import atomic_write_json, reject_nonfinite, utcnow_iso
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# JSON-LD context (shared by the whole graph)
# ---------------------------------------------------------------------------

_CONTEXT: dict[str, Any] = {
    "ccrsg": "https://specback.dev/ns/",
    "schema": "https://schema.org/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "ccrsg:sourceId": {"@type": "rdfs:label"},
    "ccrsg:inventoryId": {"@type": "rdfs:label"},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """Turn a spec chapter title into a URL-safe slug."""
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _line_range_str(lr: list[int] | None) -> str | None:
    if lr and len(lr) >= 2:
        return f"{lr[0]}-{lr[1]}"
    return None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        print(
            f"WARNING: {label} not found: {path} — proceeding with an empty {label}; "
            f"the knowledge graph will have fewer nodes.",
            file=sys.stderr,
        )
        return {}
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def build_source_unit_node(unit: dict[str, Any]) -> dict[str, Any]:
    """Convert a source-map unit dict into a JSON-LD node."""
    src_id = unit.get("id", "")
    node: dict[str, Any] = {
        "@id": f"ccrsg:unit/{src_id}",
        "@type": "ccrsg:SourceUnit",
        "ccrsg:sourceId": src_id,
        "schema:name": unit.get("name", ""),
        "ccrsg:role": unit.get("role", ""),
        "ccrsg:kind": unit.get("kind", ""),
        "ccrsg:path": unit.get("path", ""),
        "ccrsg:tier": unit.get("tier", "middle"),
        "ccrsg:language": unit.get("language", ""),
        "ccrsg:fingerprint": unit.get("fingerprint", ""),
    }
    lr = _line_range_str(unit.get("line_range"))
    if lr:
        node["ccrsg:lineRange"] = lr
    if unit.get("framework"):
        node["ccrsg:framework"] = unit["framework"]
    endpoint = unit.get("endpoint")
    if endpoint:
        node["ccrsg:httpMethod"] = endpoint.get("method", "")
        node["ccrsg:urlPath"] = endpoint.get("path", "")
    return node


def build_inventory_node(item: dict[str, Any], source_ids: list[str]) -> dict[str, Any]:
    """Convert an inventory item into a JSON-LD node linked to its source unit(s)."""
    inv_id = item.get("id", "")
    node: dict[str, Any] = {
        "@id": f"ccrsg:inv/{inv_id}",
        "@type": "ccrsg:InventoryItem",
        "ccrsg:inventoryId": inv_id,
        "schema:name": item.get("name", ""),
        "ccrsg:type": item.get("type", ""),
    }
    # Link to source unit(s)
    if source_ids:
        node["ccrsg:derivedFrom"] = [{"@id": f"ccrsg:unit/{sid}"} for sid in source_ids]
    return node


def build_spec_chapter_node(
    section_key: str,
    source_ids: list[str],
) -> dict[str, Any]:
    """Convert a trace.json section key into a JSON-LD SpecChapter node.

    ``section_key`` is ``"filename.md::Section Title"``.
    """
    node: dict[str, Any] = {
        "@id": f"ccrsg:chapter/{_slugify(section_key)}",
        "@type": "ccrsg:SpecChapter",
        "schema:name": section_key,
    }
    if source_ids:
        node["ccrsg:coversUnit"] = [{"@id": f"ccrsg:unit/{sid}"} for sid in source_ids]
    return node


def build_question_node(
    question: dict[str, Any],
    idx: int,
    related_source_ids: list[str],
) -> dict[str, Any]:
    """Convert a questions.json entry into a JSON-LD Question node."""
    qid = question.get("id", f"Q-{idx + 1:04d}")
    node: dict[str, Any] = {
        "@id": f"ccrsg:question/{qid}",
        "@type": "ccrsg:Question",
        "schema:name": question.get("question", question.get("text", "")),
        "ccrsg:category": question.get("category", ""),
        "ccrsg:severity": question.get("severity", ""),
        "ccrsg:status": question.get("status", "open"),
    }
    if related_source_ids:
        node["ccrsg:raisesForUnit"] = [{"@id": f"ccrsg:unit/{sid}"} for sid in related_source_ids]
    return node


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_knowledge_graph(
    source_map: dict[str, Any],
    inventory: dict[str, Any],
    trace: dict[str, Any],
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the full JSON-LD graph from all input sources."""
    graph: list[dict[str, Any]] = []

    # Index source-map units by ID for fast lookup
    units_by_id: dict[str, dict[str, Any]] = {}
    for u in source_map.get("units", []):
        uid = u.get("id", "")
        if uid:
            units_by_id[uid] = u
            graph.append(build_source_unit_node(u))

    # Build inventory nodes linked to source units
    inv_items = inventory.get("units", []) if isinstance(inventory, dict) else []
    for item in inv_items:
        source_ids: list[str] = item.get("related_source_ids", [])
        graph.append(build_inventory_node(item, source_ids))

    # Build SpecChapter nodes from trace.json's by_section
    by_section = trace.get("by_section", {}) if isinstance(trace, dict) else {}
    for section_key, src_ids in by_section.items():
        graph.append(build_spec_chapter_node(section_key, src_ids))

    # Build Question nodes
    if questions:
        # Build a map from source ID → questions for raisesForUnit links
        for idx, q in enumerate(questions):
            q_source_ids: list[str] = q.get("related_source_ids") or q.get("source_ids") or []
            graph.append(build_question_node(q, idx, q_source_ids))

    # Assemble top-level document
    doc: dict[str, Any] = {
        "@context": _CONTEXT,
        "@graph": graph,
        "ccrsg:generatedAt": utcnow_iso(),
        "ccrsg:schemaVersion": "0.1.0",
        "ccrsg:stats": {
            "sourceUnits": len(units_by_id),
            "inventoryItems": len(inv_items),
            "specChapters": len(by_section),
            "questions": len(questions) if questions else 0,
            "totalNodes": len(graph),
        },
    }
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a JSON-LD Knowledge Graph from source-map, trace, and inventory",
    )
    p.add_argument(
        "--source-map", type=Path, default=".specback/source-map.json",
        help="Path to source-map.json (default: .specback/source-map.json)",
    )
    p.add_argument(
        "--trace", type=Path, default=".specback/trace.json",
        help="Path to trace.json (default: .specback/trace.json)",
    )
    p.add_argument(
        "--inventory", type=Path, default=".specback/inventory.json",
        help="Path to inventory.json (default: .specback/inventory.json)",
    )
    p.add_argument(
        "--questions", type=Path, default=None,
        help="Path to questions.json (optional; auto-detected from --specback-dir if omitted)",
    )
    p.add_argument(
        "--specback-dir", type=Path, default=None,
        help="Path to .specback/ directory (used to auto-discover questions.json)",
    )
    p.add_argument(
        "--output", type=Path, default=".specback/knowledge-graph.jsonld",
        help="Path to output JSON-LD file (default: .specback/knowledge-graph.jsonld)",
    )
    p.add_argument(
        "--skip-questions", action="store_true",
        help="Skip questions.json even if present (useful when questions do not exist yet)",
    )
    p.add_argument(
        "--skip-kg", action="store_true",
        help="Exit immediately with code 0 (convenience flag for the Phase 6 pipeline)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # --skip-kg: no-op exit for Phase 6 pipeline convenience
    if args.skip_kg:
        print("build-knowledge-graph: --skip-kg set, exiting.")
        return 0

    # Load source-map (required)
    source_map = _load_json(args.source_map, "source-map.json")
    if not source_map or "units" not in source_map:
        print(f"ERROR: {args.source_map} is missing or has no 'units' key.", file=sys.stderr)
        return 2

    # Load trace (optional — produces fewer chapter nodes if absent)
    trace = _load_json(args.trace, "trace.json")

    # Load inventory (optional — produces fewer nodes if absent)
    inventory = _load_json(args.inventory, "inventory.json")

    # Auto-discover questions path from --specback-dir if --questions not given
    questions_path: Path | None = args.questions
    if questions_path is None and args.specback_dir is not None:
        questions_path = args.specback_dir / "questions.json"
    elif questions_path is None:
        # Try default location
        questions_path = Path(".specback/questions.json")

    questions: list[dict[str, Any]] | None = None
    if args.skip_questions:
        questions = None
    elif questions_path and questions_path.exists():
        raw = _load_json(questions_path, "questions.json")
        if isinstance(raw, dict):
            questions = raw.get("questions", [])
        elif isinstance(raw, list):
            questions = raw
        else:
            questions = None
        if questions:
            print(f"build-knowledge-graph: loaded {len(questions)} questions from {questions_path}")
    else:
        questions = None

    graph = build_knowledge_graph(source_map, inventory, trace, questions)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output, graph)

    stats = graph["ccrsg:stats"]
    print(
        f"build-knowledge-graph: {stats['totalNodes']} nodes "
        f"({stats['sourceUnits']} source units, {stats['inventoryItems']} inventory items, "
        f"{stats['specChapters']} spec chapters, {stats['questions']} questions) "
        f"→ {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
