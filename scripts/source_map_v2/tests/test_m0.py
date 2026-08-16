"""M0 acceptance tests: constitution + schema 0.2.0 + three-layer skeleton.

Run from the scripts/ directory:
    python -m pytest source_map_v2/tests/test_m0.py -q
"""

from __future__ import annotations

import json

import pytest

from source_map_v2 import SCHEMA_VERSION, build_source_map
from source_map_v2 import extractors, taxonomy
from source_map_v2.extractors import tshelpers
from source_map_v2.model import SourceMap, SourceUnit


def _ts_available(language: str) -> bool:
    """Check whether a tree-sitter grammar is installed for *language*."""
    return tshelpers.have(language)


ts_not_available = pytest.mark.skipif(
    not _ts_available("kotlin"),
    reason="tree-sitter-kotlin grammar not installed (pip install -r requirements.txt)",
)
ts_py_not_available = pytest.mark.skipif(
    not _ts_available("python"),
    reason="tree-sitter-python grammar not installed (pip install -r requirements.txt)",
)


# --------------------------------------------------------------------------
# Constitution (taxonomy)
# --------------------------------------------------------------------------

def test_every_role_maps_to_a_universal_table():
    for role in taxonomy.ROLES:
        assert taxonomy.table_for_role(role) in taxonomy.UNIVERSAL_TABLES


def test_register_kind_binds_role_and_tier():
    taxonomy.register_kind("test_widget", "schema", "micro")
    assert taxonomy.role_for_kind("test_widget") == "schema"
    assert taxonomy.tier_for_kind("test_widget") == "micro"


def test_register_kind_rejects_unknown_role_and_tier():
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.register_kind("bad_role_kind", "not_a_role")
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.register_kind("bad_tier_kind", "class", "nano")


def test_register_kind_refuses_conflicting_rebind():
    taxonomy.register_kind("stable_kind", "class")
    taxonomy.register_kind("stable_kind", "class")  # idempotent ok
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.register_kind("stable_kind", "endpoint")


# --------------------------------------------------------------------------
# Schema 0.2.0 (model)
# --------------------------------------------------------------------------

def test_unit_to_dict_carries_table_and_endpoint():
    u = SourceUnit(
        id="SRC-0001", path="a/api.py", line_range=(10, 20), language="python",
        role="endpoint", kind="fastapi_endpoint", name="create", tier="middle",
        framework="fastapi", endpoint={"method": "POST", "path": "/x"},
    )
    u.validate()
    d = u.to_dict()
    assert d["table"] == taxonomy.TABLE_ACTIONS
    assert d["endpoint"] == {"method": "POST", "path": "/x"}
    assert d["framework"] == "fastapi"
    assert d["line_range"] == [10, 20]


def test_endpoint_metadata_only_on_endpoint_role():
    bad = SourceUnit(
        id="SRC-0002", path="a.py", line_range=(1, 2), language="python",
        role="class", kind="py_class", name="X", endpoint={"method": "GET", "path": "/"},
    )
    with pytest.raises(taxonomy.TaxonomyError):
        bad.validate()


def test_sourcemap_stats_and_schema_version():
    sm = SourceMap(target_root="demo")
    sm.units = [
        SourceUnit("SRC-0001", "a.py", (1, 2), "python", "class", "py_class", "A"),
        SourceUnit("SRC-0002", "a.py", (3, 9), "python", "endpoint", "py_endpoint", "f",
                   endpoint={"method": "GET", "path": "/"}),
        SourceUnit("SRC-0003", "b.ts", (1, 1), "typescript", "schema", "ts_interface", "T"),
    ]
    sm.files_scanned = 2
    payload = sm.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION == "0.2.0"
    stats = payload["stats"]
    assert stats["units_total"] == 3
    assert stats["by_role"] == {"class": 1, "endpoint": 1, "schema": 1}
    assert stats["by_language"] == {"python": 2, "typescript": 1}
    # round-trips through JSON
    assert json.loads(json.dumps(payload))["stats"]["units_total"] == 3


# --------------------------------------------------------------------------
# Three-layer skeleton (pipeline)
# --------------------------------------------------------------------------

def _make_project(tmp_path):
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (root / "app" / "ui.ts").write_text("export const x = 1\n", encoding="utf-8")
    (root / "legacy.php").write_text("<?php class C {}\n", encoding="utf-8")
    (root / "legacy.kt").write_text("fun main() {}\n", encoding="utf-8")  # kotlin: no extractor yet
    (root / "README.txt").write_text("not source\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi==0.110\n", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"dependencies": {"next": "14"}}), encoding="utf-8")
    return root


def test_layer1_framework_detection_with_evidence(tmp_path):
    root = _make_project(tmp_path)
    payload = build_source_map(root).to_dict()
    fws = {h["framework"] for h in payload["detected_frameworks"]}
    assert "fastapi" in fws and "nextjs" in fws
    assert all(h["evidence"] for h in payload["detected_frameworks"])


@ts_not_available
def test_unsupported_language_falls_back_with_loud_warning(tmp_path):
    """A recognised language with no extractor must warn, not vanish (P4).

    All extensions in ``LANG_BY_EXT`` now have extractors, so we use a
    file that has no entry at all — it should be excluded (no warning).

    Requires tree-sitter-kotlin grammar (skip when unavailable).
    """
    root = _make_project(tmp_path)
    payload = build_source_map(root).to_dict()
    # legacy.kt is now supported, no warning expected
    assert not any("'kotlin'" in w for w in payload["warnings"])
    kt_units = [u for u in payload["units"] if u["language"] == "kotlin"]
    assert kt_units and kt_units[0]["kind"] == "kotlin_function"  # extracted properly
    assert payload["stats"]["files_excluded"] >= 1            # README.txt excluded


@ts_py_not_available
def test_supported_languages_are_not_warned(tmp_path):
    """python/typescript are autoloaded; they must be extracted, not warned.

    Requires tree-sitter-python grammar (skip when unavailable).
    """
    root = _make_project(tmp_path)
    payload = build_source_map(root).to_dict()
    assert not any("'python'" in w for w in payload["warnings"])
    assert not any("'typescript'" in w for w in payload["warnings"])
    # main.py's `def hello()` was really extracted as a callable
    assert any(u["language"] == "python" and u["role"] == "callable" and u["name"] == "hello"
               for u in payload["units"])


def test_registered_extractor_is_dispatched_with_framework(tmp_path):
    """Prove layer-2 dispatch + framework hand-off via a dummy extractor on php."""
    taxonomy.register_kind("dummy_endpoint", "endpoint", "middle")

    class DummyPhp(extractors.Extractor):
        language = "php"

        def extract(self, path, source, id_factory, framework=None, context=None):
            return [SourceUnit(
                id=id_factory(), path=path, line_range=(1, 1), language="php",
                role="endpoint", kind="dummy_endpoint", name="dummy",
                framework=framework, endpoint={"method": "GET", "path": "/dummy"},
            )]

    saved = extractors.get_extractor("php")
    extractors.register(DummyPhp())
    try:
        root = _make_project(tmp_path)
        payload = build_source_map(root).to_dict()
        php_units = [u for u in payload["units"] if u["language"] == "php"]
        assert any(u["role"] == "endpoint" and u["kind"] == "dummy_endpoint" for u in php_units)
        assert not any("'php'" in w for w in payload["warnings"])  # php now handled
    finally:
        if saved is None:
            extractors._REGISTRY.pop("php", None)
        else:
            extractors._REGISTRY["php"] = saved


def test_missing_grammar_warns_with_install_hint(tmp_path, monkeypatch):
    """A tree-sitter backed language without its grammar must point to the fix.

    Regression guard for #110: the old wording ("has no v2 extractor yet")
    reads as "feature not implemented" when the real cause is a missing
    optional dependency, which led users to file duplicate issues.
    """
    root = _make_project(tmp_path)
    # Simulate the python grammar being unavailable (as if deps not installed).
    original_state = tshelpers.install_state
    monkeypatch.setattr(
        tshelpers, "install_state",
        lambda lang: tshelpers.STATE_MISSING if lang == "python" else original_state(lang),
    )
    monkeypatch.setattr(
        extractors, "get_extractor",
        lambda lang: None if lang == "python" else extractors._REGISTRY.get(lang),
    )
    payload = build_source_map(root).to_dict()
    py_warns = [w for w in payload["warnings"] if "'python'" in w]
    assert len(py_warns) == 1
    assert "tree-sitter" in py_warns[0]
    assert "pip install tree-sitter-python" in py_warns[0]
    assert "no v2 extractor yet" not in py_warns[0]
    assert "did not register" not in py_warns[0]
    # fallback still emits file-level units, so nothing silently vanishes
    assert any(u["language"] == "python" for u in payload["units"])


def test_extractor_import_failure_warns_not_grammar(tmp_path, monkeypatch):
    """Grammar installed but extractor missing → warn about the module, not pip.

    The install hint must NOT be shown when the grammar is present — the real
    cause is a module-level failure in the extractor (e.g. _autoload's
    `except Exception: pass` swallowing an import bug).
    """
    root = _make_project(tmp_path)
    # python grammar IS available; simulate the extractor failing to register.
    original_install_state = tshelpers.install_state  # save before monkeypatch
    monkeypatch.setattr(
        tshelpers, "install_state",
        lambda lang: tshelpers.STATE_OK if lang == "python" else original_install_state(lang),  # noqa: E731
    )
    monkeypatch.setattr(
        extractors, "get_extractor",
        lambda lang: None if lang == "python" else extractors._REGISTRY.get(lang),  # noqa: E731
    )
    payload = build_source_map(root).to_dict()
    py_warns = [w for w in payload["warnings"] if "'python'" in w]
    assert len(py_warns) == 1
    assert "did not register" in py_warns[0]
    assert "pip install" not in py_warns[0]
    assert any(u["language"] == "python" for u in payload["units"])


def test_unimplemented_language_keeps_legacy_warning(tmp_path, monkeypatch):
    """Languages with no v2 extractor at all keep the "not implemented" wording.

    sql/cobol are regex-based and always register; forcing get_extractor to
    None simulates a hypothetical future language without any extractor.
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "requirements.txt").write_text("", encoding="utf-8")  # root marker
    (root / "query.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(
        extractors, "get_extractor",
        lambda lang: None if lang == "sql" else extractors._REGISTRY.get(lang),
    )
    payload = build_source_map(root).to_dict()
    sql_warns = [w for w in payload["warnings"] if "'sql'" in w]
    assert len(sql_warns) == 1
    assert "no v2 extractor yet" in sql_warns[0]
    assert "pip install" not in sql_warns[0]
    assert any(u["language"] == "sql" for u in payload["units"])


def test_iter_files_does_not_follow_symlink_outside_target(tmp_path):
    """Regression guard for #317: symlinks must never be followed, so a link
    inside the target that points OUTSIDE it is not read into the source map."""
    # Hostile file outside the scanned tree
    outside = tmp_path / "secret.txt"
    outside.write_text("TOPSECRET", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    # A symlink inside the tree pointing outside it
    leak = src / "leak.txt"
    try:
        leak.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported on this platform")

    smap = build_source_map(src, exclude_globs=[])
    payload = smap.to_dict()
    # The secret must NOT appear anywhere in the payload.
    assert "TOPSECRET" not in json.dumps(payload)
    # No unit should have been created from the symlink (it is skipped).
    rel = str(leak.relative_to(src.parent))  # pipeline rel is relative to src.parent
    for u in payload["units"]:
        assert u.get("path") != rel

