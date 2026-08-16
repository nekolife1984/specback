"""Tests for build-knowledge-graph.py.

Tests both the CLI interface and the graph building logic using temp files.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parent.parent / "build-knowledge-graph.py"

# Ensure scripts/ is importable — build-knowledge-graph.py imports shared
# helpers (common.py) from its own directory.
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

# Load the script module once via importlib so the node-builder / helper
# functions can be exercised directly (Issue #325) rather than only through the
# subprocess CLI.
import importlib.util

_kg_spec = importlib.util.spec_from_file_location("build_kg_src", SCRIPT)
assert _kg_spec is not None and _kg_spec.loader is not None
_kg_mod = importlib.util.module_from_spec(_kg_spec)
_kg_spec.loader.exec_module(_kg_mod)

_slugify = _kg_mod._slugify
_line_range_str = _kg_mod._line_range_str
build_source_unit_node = _kg_mod.build_source_unit_node
build_inventory_node = _kg_mod.build_inventory_node
build_spec_chapter_node = _kg_mod.build_spec_chapter_node
build_question_node = _kg_mod.build_question_node
build_knowledge_graph = _kg_mod.build_knowledge_graph

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

SAMPLE_SOURCE_MAP = {
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

SAMPLE_TRACE = {
    "schema_version": "0.2.0",
    "generated_at": "2026-07-29T21:00:00",
    "source_units_total": 4,
    "source_units_covered": 2,
    "source_units_excluded": 0,
    "source_units_uncovered": 2,
    "mece_passed": False,
    "by_section": {
        "03-api-endpoints.md::3.1 REST Endpoints": ["SRC-0001"],
        "05-data-model.md::5.2 Product Model": ["SRC-0003"],
    },
    "by_source": {},
    "uncovered_units": ["SRC-0002", "SRC-0004"],
}

SAMPLE_INVENTORY = {
    "units": [
        {
            "id": "INV-0001", "type": "api_endpoint", "name": "create_item",
            "file": "app/main.py", "line": 10,
            "covered_by": ["03-api-endpoints.md::3.1 REST Endpoints"],
            "related_source_ids": ["SRC-0001"],
        },
        {
            "id": "INV-0002", "type": "schema", "name": "Item",
            "file": "app/schemas.py", "line": 5,
            "covered_by": [],
            "related_source_ids": ["SRC-0002"],
        },
        {
            "id": "INV-0003", "type": "orm_model", "name": "Product",
            "file": "app/models.py", "line": 1,
            "covered_by": ["05-data-model.md::5.2 Product Model"],
            "related_source_ids": ["SRC-0003"],
        },
    ],
}

SAMPLE_QUESTIONS = {
    "questions": [
        {
            "id": "Q-0001", "category": "architecture",
            "severity": "high", "status": "open",
            "question": "What is the auth strategy?",
            "related_source_ids": ["SRC-0001"],
        },
        {
            "id": "Q-0002", "category": "data_model",
            "severity": "medium", "status": "resolved",
            "question": "Should Product have a status field?",
            "related_source_ids": ["SRC-0003"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_script(tmp_path: Path, args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT)]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def load_kg(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(tmp_path: Path, data: dict, name: str) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# CLI interface tests
# ---------------------------------------------------------------------------


def test_help():
    result = run_script(None, ["--help"])
    assert result.returncode == 0
    assert "--source-map" in result.stdout
    assert "--output" in result.stdout
    assert "--skip-kg" in result.stdout


def test_skip_kg(tmp_path: Path):
    """--skip-kg should exit immediately with code 0, no output written."""
    out = tmp_path / "kg.jsonld"
    result = run_script(tmp_path, ["--skip-kg", "--output", str(out)])
    assert result.returncode == 0
    assert not out.exists()


def test_missing_source_map(tmp_path: Path):
    result = run_script(tmp_path, ["--source-map", str(tmp_path / "nope.json")])
    assert result.returncode == 2
    assert "missing" in result.stderr or "ERROR" in result.stderr


def test_no_units_key(tmp_path: Path):
    sm = tmp_path / "bad-sm.json"
    sm.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    result = run_script(tmp_path, ["--source-map", str(sm)])
    assert result.returncode == 2
    assert "no 'units'" in result.stderr


# ---------------------------------------------------------------------------
# Functional tests
# ---------------------------------------------------------------------------


def test_basic_graph_structure(tmp_path: Path):
    """Full pipeline should produce a valid JSON-LD graph."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])
    assert result.returncode == 0, f"stderr: {result.stderr}"

    kg = load_kg(out)
    assert "@context" in kg
    assert "@graph" in kg
    assert "ccrsg:schemaVersion" in kg


def test_source_unit_nodes(tmp_path: Path):
    """Each source-map unit becomes a SourceUnit node with correct properties."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    out = tmp_path / "kg.jsonld"

    run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])

    kg = load_kg(out)
    units = [n for n in kg["@graph"] if n.get("@type") == "ccrsg:SourceUnit"]
    assert len(units) == 4

    # Check SRC-0001 has endpoint metadata
    ep = next(n for n in units if n["ccrsg:sourceId"] == "SRC-0001")
    assert ep["ccrsg:httpMethod"] == "POST"
    assert ep["ccrsg:urlPath"] == "/items"
    assert ep["ccrsg:role"] == "endpoint"
    assert ep["ccrsg:lineRange"] == "10-30"

    # Check SRC-0002 has no endpoint
    schema = next(n for n in units if n["ccrsg:sourceId"] == "SRC-0002")
    assert "ccrsg:httpMethod" not in schema
    assert schema["ccrsg:role"] == "schema"


def test_inventory_nodes(tmp_path: Path):
    """Inventory items become InventoryItem nodes linked to source units."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    out = tmp_path / "kg.jsonld"

    run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])

    kg = load_kg(out)
    inv_nodes = [n for n in kg["@graph"] if n.get("@type") == "ccrsg:InventoryItem"]
    assert len(inv_nodes) == 3

    inv1 = next(n for n in inv_nodes if n["ccrsg:inventoryId"] == "INV-0001")
    assert inv1["schema:name"] == "create_item"
    assert inv1["ccrsg:type"] == "api_endpoint"
    assert "ccrsg:derivedFrom" in inv1
    derived_ids = [r["@id"] for r in inv1["ccrsg:derivedFrom"]]
    assert "ccrsg:unit/SRC-0001" in derived_ids


def test_spec_chapter_nodes(tmp_path: Path):
    """Trace sections become SpecChapter nodes with coversUnit relations."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    out = tmp_path / "kg.jsonld"

    run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])

    kg = load_kg(out)
    chapters = [n for n in kg["@graph"] if n.get("@type") == "ccrsg:SpecChapter"]
    assert len(chapters) == 2  # 2 sections in trace

    ch = next(n for n in chapters if "REST Endpoints" in n["schema:name"])
    assert "ccrsg:coversUnit" in ch
    covered = [r["@id"] for r in ch["ccrsg:coversUnit"]]
    assert "ccrsg:unit/SRC-0001" in covered


def test_question_nodes(tmp_path: Path):
    """Questions from questions.json become Question nodes with raisesForUnit."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    write_json(tmp_path, SAMPLE_QUESTIONS, "questions.json")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--questions", str(tmp_path / "questions.json"),
        "--output", str(out),
    ])
    assert result.returncode == 0

    kg = load_kg(out)
    q_nodes = [n for n in kg["@graph"] if n.get("@type") == "ccrsg:Question"]
    assert len(q_nodes) == 2

    q1 = next(n for n in q_nodes if "auth strategy" in n["schema:name"])
    assert q1["ccrsg:category"] == "architecture"
    assert q1["ccrsg:severity"] == "high"
    assert q1["ccrsg:status"] == "open"
    assert "ccrsg:raisesForUnit" in q1


def test_stats(tmp_path: Path):
    """Stats should reflect the correct counts."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    write_json(tmp_path, SAMPLE_QUESTIONS, "questions.json")
    out = tmp_path / "kg.jsonld"

    run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--questions", str(tmp_path / "questions.json"),
        "--output", str(out),
    ])

    kg = load_kg(out)
    stats = kg.get("ccrsg:stats", {})
    assert stats["sourceUnits"] == 4
    assert stats["inventoryItems"] == 3
    assert stats["specChapters"] == 2
    assert stats["questions"] == 2
    assert stats["totalNodes"] == 4 + 3 + 2 + 2  # units + inv + chapters + questions


def test_empty_trace_and_inventory(tmp_path: Path):
    """Should still work when trace.json / inventory.json have no data."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")

    empty_trace = {"schema_version": "0.2.0", "by_section": {}}
    empty_inv: dict[str, Any] = {"units": []}
    write_json(tmp_path, empty_trace, "trace.json")
    write_json(tmp_path, empty_inv, "inventory.json")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])
    assert result.returncode == 0

    kg = load_kg(out)
    stats = kg["ccrsg:stats"]
    assert stats["sourceUnits"] == 4
    assert stats["inventoryItems"] == 0
    assert stats["specChapters"] == 0


def test_skip_questions_flag(tmp_path: Path):
    """--skip-questions suppresses loading questions.json even if present."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    write_json(tmp_path, SAMPLE_TRACE, "trace.json")
    write_json(tmp_path, SAMPLE_INVENTORY, "inventory.json")
    write_json(tmp_path, SAMPLE_QUESTIONS, "questions.json")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "trace.json"),
        "--inventory", str(tmp_path / "inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])
    assert result.returncode == 0

    kg = load_kg(out)
    q_nodes = [n for n in kg["@graph"] if n.get("@type") == "ccrsg:Question"]
    assert len(q_nodes) == 0
    assert kg["ccrsg:stats"]["questions"] == 0


def test_missing_optional_input_warns_consequence(tmp_path: Path):
    """Missing trace/inventory warns the graph will be incomplete, not silently."""
    write_json(tmp_path, SAMPLE_SOURCE_MAP, "source-map.json")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(tmp_path / "source-map.json"),
        "--trace", str(tmp_path / "missing-trace.json"),
        "--inventory", str(tmp_path / "missing-inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])
    assert result.returncode == 0
    assert "proceeding with an empty trace.json" in result.stderr
    assert "proceeding with an empty inventory.json" in result.stderr
    assert "knowledge graph will have fewer nodes" in result.stderr


def test_nonfinite_input_is_rejected_cleanly(tmp_path: Path):
    """NaN inside source-map.json must be rejected (reject_nonfinite) and
    handled cleanly (no raw traceback), Issue #314."""
    sm = tmp_path / "source-map.json"
    sm.write_text('{"units": [], "bad": NaN}', encoding="utf-8")
    out = tmp_path / "kg.jsonld"

    result = run_script(tmp_path, [
        "--source-map", str(sm),
        "--trace", str(tmp_path / "missing-trace.json"),
        "--inventory", str(tmp_path / "missing-inventory.json"),
        "--skip-questions",
        "--output", str(out),
    ])
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "missing or has no 'units' key" in result.stderr


# ---------------------------------------------------------------------------
# Direct function-level tests (Issue #325 — importlib, not subprocess)
# ---------------------------------------------------------------------------


class TestSlugifyDirect:
    def test_basic(self):
        assert _slugify("3.1 REST Endpoints") == "3-1-rest-endpoints"

    def test_collapses_runs_of_separators(self):
        assert _slugify("  Foo :: Bar?? ") == "foo-bar"

    def test_already_slug_lower(self):
        assert _slugify("api-endpoints") == "api-endpoints"

    def test_empty_and_special_only(self):
        assert _slugify("") == ""
        assert _slugify("!!!") == ""


class TestLineRangeStrDirect:
    def test_two_element_list(self):
        assert _line_range_str([10, 30]) == "10-30"

    def test_none_and_single_element(self):
        assert _line_range_str(None) is None
        assert _line_range_str([10]) is None

    def test_empty_list(self):
        assert _line_range_str([]) is None


class TestBuildSourceUnitNodeDirect:
    def test_endpoint_unit(self):
        unit = SAMPLE_SOURCE_MAP["units"][0]
        node = build_source_unit_node(unit)
        assert node["@id"] == "ccrsg:unit/SRC-0001"
        assert node["@type"] == "ccrsg:SourceUnit"
        assert node["ccrsg:sourceId"] == "SRC-0001"
        assert node["ccrsg:lineRange"] == "10-30"
        assert node["ccrsg:httpMethod"] == "POST"
        assert node["ccrsg:urlPath"] == "/items"
        assert node["ccrsg:role"] == "endpoint"

    def test_non_endpoint_unit(self):
        unit = SAMPLE_SOURCE_MAP["units"][1]
        node = build_source_unit_node(unit)
        assert "ccrsg:httpMethod" not in node
        assert "ccrsg:urlPath" not in node
        assert node["ccrsg:role"] == "schema"

    def test_no_line_range(self):
        node = build_source_unit_node({"id": "SRC-X", "name": "n"})
        assert "ccrsg:lineRange" not in node


class TestBuildInventoryNodeDirect:
    def test_with_source_ids(self):
        item = SAMPLE_INVENTORY["units"][0]
        node = build_inventory_node(item, item["related_source_ids"])
        assert node["@id"] == "ccrsg:inv/INV-0001"
        assert node["@type"] == "ccrsg:InventoryItem"
        assert node["schema:name"] == "create_item"
        derived = [r["@id"] for r in node["ccrsg:derivedFrom"]]
        assert "ccrsg:unit/SRC-0001" in derived

    def test_without_source_ids(self):
        node = build_inventory_node({"id": "INV-9", "name": "x"}, [])
        assert "ccrsg:derivedFrom" not in node


class TestBuildSpecChapterNodeDirect:
    def test_basic(self):
        key = "04-queries.md::4.2 Search Queries"
        node = build_spec_chapter_node(key, ["SRC-0001", "SRC-0003"])
        assert node["@type"] == "ccrsg:SpecChapter"
        assert node["schema:name"] == key
        assert node["@id"] == f"ccrsg:chapter/{_slugify(key)}"
        covered = [r["@id"] for r in node["ccrsg:coversUnit"]]
        assert covered == ["ccrsg:unit/SRC-0001", "ccrsg:unit/SRC-0003"]

    def test_no_source_ids(self):
        node = build_spec_chapter_node("a.md::B", [])
        assert "ccrsg:coversUnit" not in node


class TestBuildQuestionNodeDirect:
    def test_basic(self):
        q = SAMPLE_QUESTIONS["questions"][0]
        node = build_question_node(q, 0, q["related_source_ids"])
        assert node["@id"] == "ccrsg:question/Q-0001"
        assert node["@type"] == "ccrsg:Question"
        assert node["schema:name"] == "What is the auth strategy?"
        assert node["ccrsg:category"] == "architecture"
        assert node["ccrsg:severity"] == "high"
        assert node["ccrsg:status"] == "open"
        raises = [r["@id"] for r in node["ccrsg:raisesForUnit"]]
        assert "ccrsg:unit/SRC-0001" in raises

    def test_generated_id_and_default_status(self):
        node = build_question_node({"question": "T? 中文", "category": "x"}, 3, [])
        assert node["@id"] == "ccrsg:question/Q-0004"
        assert node["ccrsg:status"] == "open"
        assert "ccrsg:raisesForUnit" not in node


class TestBuildKnowledgeGraphDirect:
    def test_aggregates_all_node_types(self):
        kg = build_knowledge_graph(
            SAMPLE_SOURCE_MAP,
            SAMPLE_INVENTORY,
            SAMPLE_TRACE,
            SAMPLE_QUESTIONS["questions"],
        )
        graph = kg["@graph"]
        types = {}
        for n in graph:
            types.setdefault(n["@type"], 0)
            types[n["@type"]] += 1
        assert types["ccrsg:SourceUnit"] == 4
        assert types["ccrsg:InventoryItem"] == 3
        assert types["ccrsg:SpecChapter"] == 2
        assert types["ccrsg:Question"] == 2

        stats = kg["ccrsg:stats"]
        assert stats["sourceUnits"] == 4
        assert stats["inventoryItems"] == 3
        assert stats["specChapters"] == 2
        assert stats["questions"] == 2
        assert stats["totalNodes"] == len(graph)
        assert kg["ccrsg:schemaVersion"] == "0.1.0"

    def test_empty_inputs(self):
        kg = build_knowledge_graph({"units": []}, {"units": []}, {}, None)
        assert kg["@graph"] == []
        assert kg["ccrsg:stats"]["totalNodes"] == 0

    def test_inventory_node_links_to_related_source(self):
        kg = build_knowledge_graph(SAMPLE_SOURCE_MAP, SAMPLE_INVENTORY, {}, None)
        inv = next(
            n for n in kg["@graph"]
            if n.get("@type") == "ccrsg:InventoryItem" and n["schema:name"] == "Product"
        )
        derived = [r["@id"] for r in inv["ccrsg:derivedFrom"]]
        assert "ccrsg:unit/SRC-0003" in derived
