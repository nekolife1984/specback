"""
Direct unit tests for coverage-check.py core check functions (Issue #258).

Unlike the e2e tests in test_coverage_check_code_blocks.py (which run the
script with all thresholds zeroed), these tests import the module and call
the detection / gate-evaluation logic directly, so regressions are caught
even when e2e thresholds are relaxed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "coverage-check.py"

# Ensure scripts/ is importable — coverage-check.py imports count_refs from
# scripts/refutils.py (Issue #281).
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("coverage_check_core", SCRIPT)
assert _spec is not None and _spec.loader is not None
# Dynamically loaded module — mypy cannot resolve its attributes, so it is
# typed as Any (ChapterMetrics / evaluate_chapter_gates live in the script).
cov: Any = importlib.util.module_from_spec(_spec)
sys.modules["coverage_check_core"] = cov
_spec.loader.exec_module(cov)


# ---------------------------------------------------------------------------
# evaluate_chapter_gates (Issue #258: threshold+1 failure detection)
# ---------------------------------------------------------------------------


def _metrics(**overrides) -> cov.ChapterMetrics:
    defaults = dict(
        file="01-overview.md",
        total_lines=120,
        body_lines=60,
        refs=8,
        code_blocks=2,
        mermaid_blocks=0,
    )
    defaults.update(overrides)
    return cov.ChapterMetrics(**defaults)


def test_evaluate_chapter_gates_fails_below_threshold():
    m = _metrics(refs=5)
    cov.evaluate_chapter_gates(
        [m], min_refs=10, min_lines=50, min_code_blocks=1,
        min_mermaid=0,
    )
    assert any("REF" in f and "10" in f for f in m.failures), m.failures


def test_evaluate_chapter_gates_passes_at_threshold():
    m = _metrics(refs=10, body_lines=60, code_blocks=1)
    cov.evaluate_chapter_gates(
        [m], min_refs=10, min_lines=50, min_code_blocks=1,
        min_mermaid=0,
    )
    assert m.failures == [], m.failures


def test_evaluate_chapter_gates_skips_reserved_files():
    m = cov.ChapterMetrics(
        file="00-metadata.md", total_lines=3, body_lines=1, refs=0,
        code_blocks=0, mermaid_blocks=0,
    )
    cov.evaluate_chapter_gates(
        [m], min_refs=10, min_lines=50, min_code_blocks=1,
        min_mermaid=1,
    )
    assert m.failures == []


def test_evaluate_chapter_gates_weighted_body_lines():
    """code-block lines count at code_block_line_weight toward min_lines."""
    m = _metrics(body_lines=40, code_block_lines=20)  # 40 + 20*0.5 = 50
    cov.evaluate_chapter_gates(
        [m], min_refs=0, min_lines=50, min_code_blocks=0,
        min_mermaid=0, code_block_line_weight=0.5,
    )
    assert m.failures == [], m.failures


# ---------------------------------------------------------------------------
# check_naming_convention / check_required_files
# ---------------------------------------------------------------------------


def test_check_naming_convention_flags_bad_name(tmp_path):
    d = tmp_path / "drafts"
    d.mkdir()
    (d / "01-overview.md").write_text("# x\n", encoding="utf-8")
    (d / "BAD_NAME.md").write_text("# x\n", encoding="utf-8")
    warnings = cov.check_naming_convention(d)
    assert any("BAD_NAME.md" in w for w in warnings)
    assert not any("01-overview.md" in w for w in warnings)


def test_check_naming_convention_exempts_user_custom(tmp_path):
    d = tmp_path / "drafts"
    d.mkdir()
    (d / "api-guide.md").write_text("# x\n", encoding="utf-8")
    warnings = cov.check_naming_convention(d, user_custom=["api-guide.md"])
    assert warnings == []


def test_check_naming_convention_missing_dir(tmp_path):
    assert cov.check_naming_convention(tmp_path / "nope") == []


def test_check_required_files_reports_missing(tmp_path):
    missing = cov.check_required_files(tmp_path)
    assert missing, "expected missing-file report for empty dir"


def test_check_required_files_ok(tmp_path):
    for f in cov.REQUIRED_FILES:
        (tmp_path / f).write_text("# ok\n", encoding="utf-8")
    assert cov.check_required_files(tmp_path) == []


# ---------------------------------------------------------------------------
# check_user_custom_deliverables
# ---------------------------------------------------------------------------


def test_user_custom_missing_file(tmp_path):
    failures = cov.check_user_custom_deliverables(tmp_path, ["guide.md"])
    assert any("missing" in f for f in failures)


def test_user_custom_empty_body(tmp_path):
    (tmp_path / "guide.md").write_text("# Title\n", encoding="utf-8")
    failures = cov.check_user_custom_deliverables(
        tmp_path, ["guide.md"], min_body_lines=10,
    )
    assert any("body has only" in f for f in failures)


def test_user_custom_sufficient_body(tmp_path):
    (tmp_path / "guide.md").write_text(
        "\n".join(f"line {i}" for i in range(12)) + "\n", encoding="utf-8",
    )
    failures = cov.check_user_custom_deliverables(
        tmp_path, ["guide.md"], min_body_lines=10,
    )
    assert failures == []


# ---------------------------------------------------------------------------
# detect_mentions / is_macro_type
# ---------------------------------------------------------------------------


def test_detect_mentions_by_id_and_name():
    item = cov.InventoryItem(id="INV-0001", name="UserService", type="service", file="svc.rb", line=10)
    drafts = {
        "01.md": "References INV-0001 here",
        "02.md": "UserService appears by name",
        "03.md": "nothing",
    }
    mentions = cov.detect_mentions(item, drafts)
    assert "01.md" in mentions
    assert "02.md" in mentions
    assert "03.md" not in mentions


def test_is_macro_type():
    assert cov.is_macro_type(cov.InventoryItem(id="i", name="n", type="controller_group", file="x", line=1))
    assert not cov.is_macro_type(cov.InventoryItem(id="i", name="n", type="service", file="x", line=1))


# ---------------------------------------------------------------------------
# compute_chapter_metrics / check_reserved_body_lines / mermaid styling
# ---------------------------------------------------------------------------


def test_compute_chapter_metrics_counts():
    content = (
        "# Overview\n\n"
        "Some text.\n"
        "<!-- REF: app/a.py:1 -->\n\n"
        "```python\n"
        "def f():\n"
        "    pass\n"
        "```\n"
    )
    m = cov.compute_chapter_metrics("01-overview.md", content)
    assert m.file == "01-overview.md"
    assert m.refs >= 1
    assert m.code_blocks >= 1
    assert m.code_block_lines >= 1


def test_check_reserved_body_lines_fails():
    chapters = {"00-metadata.md": "# Metadata\n"}
    failures = cov.check_reserved_body_lines(chapters, min_lines=5)
    assert any("00-metadata.md" in f for f in failures)


def test_check_reserved_body_lines_passes():
    chapters = {"00-metadata.md": "# Metadata\n" + "\n".join(f"line {i}" for i in range(6)) + "\n"}
    assert cov.check_reserved_body_lines(chapters, min_lines=5) == []


def test_check_reserved_body_lines_zero_disabled():
    chapters = {"00-metadata.md": "# Metadata\n"}
    assert cov.check_reserved_body_lines(chapters, min_lines=0) == []


def test_check_mermaid_styling_flags_fill():
    chapters = {"01.md": "```mermaid\ngraph TD\n    A[Start] --> B[End]\n    style A fill:#f00\n```\n"}
    failures = cov.check_mermaid_styling(chapters)
    assert any("styling" in f for f in failures)


def test_check_mermaid_styling_clean():
    chapters = {"01.md": "```mermaid\ngraph TD\n    A[Start] --> B[End]\n```\n"}
    assert cov.check_mermaid_styling(chapters) == []


def test_check_mermaid_syntax_parens_detected():
    chapters = {"01.md": "```mermaid\ngraph TD\n    E -->|OpenAIModel (API)| P\n```\n"}
    failures = cov.check_mermaid_syntax(chapters)
    assert any("edge label" in f for f in failures)


# ---------------------------------------------------------------------------
# detect_depth_mode / render_json
# ---------------------------------------------------------------------------


def test_detect_depth_mode_default(tmp_path):
    assert cov.detect_depth_mode(tmp_path) == "comprehensive"


def test_detect_depth_mode_from_goal(tmp_path):
    (tmp_path / "goal.json").write_text(
        json.dumps({"depth_mode": "outline"}), encoding="utf-8",
    )
    assert cov.detect_depth_mode(tmp_path) == "outline"


def test_detect_depth_mode_invalid_falls_back(tmp_path):
    (tmp_path / "goal.json").write_text(
        json.dumps({"depth_mode": "bogus"}), encoding="utf-8",
    )
    assert cov.detect_depth_mode(tmp_path) == "comprehensive"


def test_render_json_includes_contract_keys():
    """JSON contract used by gates.py must stay stable (Issue #256)."""
    report = cov.CoverageReport(
        total_inventory=1, covered=1, mece_total=2, mece_covered=2,
        mece_passed_strict=True, mece_coverage_rate=1.0,
    )
    data = json.loads(cov.render_json(report))
    assert "mece_passed_strict" in data
    assert data["mece_passed_strict"] is True
    assert data["mece_coverage_rate"] == 1.0
    assert data["total_inventory"] == 1


# ---------------------------------------------------------------------------
# Loaders (inventory / questions / source-map / trace / user-custom)
# ---------------------------------------------------------------------------


def test_load_inventory(tmp_path):
    p = tmp_path / "inventory.json"
    p.write_text(json.dumps({"units": [
        {"id": "INV-1", "type": "service", "name": "UserSvc",
         "file": "svc.rb", "line": 3},
    ]}), encoding="utf-8")
    items = cov.load_inventory(p)
    assert len(items) == 1
    assert items[0].id == "INV-1"
    assert items[0].line == 3


def test_load_inventory_missing_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        cov.load_inventory(tmp_path / "nope.json")


def test_load_inventory_malformed_json_raises_valueerror(tmp_path):
    import pytest
    p = tmp_path / "inventory.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        cov.load_inventory(p)


def test_load_inventory_bare_list_raises_valueerror(tmp_path):
    import pytest
    p = tmp_path / "inventory.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        cov.load_inventory(p)


def test_load_inventory_missing_key_raises_valueerror(tmp_path):
    import pytest
    p = tmp_path / "inventory.json"
    p.write_text(json.dumps({"units": [{"name": "X"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required key"):
        cov.load_inventory(p)


def test_load_questions_dict_and_list(tmp_path):
    d = tmp_path / "q.json"
    d.write_text(json.dumps({"questions": [{"id": "Q1"}]}), encoding="utf-8")
    assert cov.load_questions(d) == [{"id": "Q1"}]

    l = tmp_path / "ql.json"
    l.write_text(json.dumps([{"id": "Q2"}]), encoding="utf-8")
    assert cov.load_questions(l) == [{"id": "Q2"}]

    missing = tmp_path / "missing.json"
    assert cov.load_questions(missing) == []


def test_load_source_map_ids(tmp_path):
    (tmp_path / "source-map.json").write_text(
        json.dumps({"units": [{"id": "SRC-1"}, {"id": "SRC-2"}]}),
        encoding="utf-8",
    )
    assert cov.load_source_map_ids(tmp_path) == {"SRC-1", "SRC-2"}


def test_load_source_map_ids_missing_or_bad(tmp_path):
    assert cov.load_source_map_ids(tmp_path) == set()
    (tmp_path / "source-map.json").write_text("{ bad", encoding="utf-8")
    assert cov.load_source_map_ids(tmp_path) == set()


def test_load_source_map_count(tmp_path):
    (tmp_path / "source-map.json").write_text(
        json.dumps({"stats": {"files_scanned": 42}}), encoding="utf-8",
    )
    assert cov.load_source_map_count(tmp_path) == 42
    assert cov.load_source_map_count(tmp_path / "empty") is None


def test_load_trace_or_none(tmp_path):
    assert cov.load_trace(tmp_path) is None
    (tmp_path / "trace.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    assert cov.load_trace(tmp_path) == {"x": 1}


def test_load_user_custom_deliverables(tmp_path):
    assert cov.load_user_custom_deliverables(tmp_path) == []
    (tmp_path / "goal.json").write_text(
        json.dumps({"user_custom_deliverables": ["api-guide.md", "BAD NAME.md"]}),
        encoding="utf-8",
    )
    assert cov.load_user_custom_deliverables(tmp_path) == ["api-guide.md"]


def test_scan_chapter_files(tmp_path):
    assert cov.scan_chapter_files(tmp_path / "nope") == {}
    d = tmp_path / "drafts"
    d.mkdir()
    (d / "01-overview.md").write_text("# Overview\n", encoding="utf-8")
    (d / "sub" / "nested.md").parent.mkdir()
    (d / "sub" / "nested.md").write_text("# nested\n", encoding="utf-8")
    chapters = cov.scan_chapter_files(d)
    assert "01-overview.md" in chapters
    assert "sub/nested.md" not in chapters  # only direct children


def test_check_question_integrity():
    questions = [
        {"id": "Q1", "category": "c", "body": "b", "severity": "critical", "status": "open"},
        {"id": "Q2", "category": "c", "body": "b", "severity": "bogus", "status": "open"},
        {"id": "Q3", "severity": "important", "status": "answered", "answer": "yes"},
    ]
    issues, blocked_referenced = cov.check_question_integrity(questions, set(), {})
    assert any("Q2" in i and "severity" in i for i in issues)
    assert any("Q3" in i and "missing" in i for i in issues)
    assert blocked_referenced == []


def test_check_question_integrity_blocked_ref():
    questions = [{"id": "Q-1", "category": "c", "body": "b", "severity": "critical", "status": "open"}]
    drafts = {"01.md": "See <!-- BLOCKED: see Q-1 --> here"}
    issues, blocked = cov.check_question_integrity(questions, set(), drafts)
    assert blocked == ["Q-1"]
    # Referencing a missing question is an issue
    drafts2 = {"01.md": "<!-- BLOCKED: see Q-NOPE -->"}
    issues2, _ = cov.check_question_integrity(questions, set(), drafts2)
    assert any("Q-NOPE" in i for i in issues2)


def test_render_text_contains_sections():
    report = cov.CoverageReport(total_inventory=5, covered=3)
    text = cov.render_text(report)
    assert "Coverage" in text or "coverage" in text


# ---------------------------------------------------------------------------
# build_report (end-to-end over a minimal specback)
# ---------------------------------------------------------------------------


def _minimal_specback(tmp_path) -> Path:
    """Create a minimal .specback dir that passes required-file checks."""
    specback = tmp_path / ".specback"
    specback.mkdir()
    final = specback / "final"
    final.mkdir()
    (specback / "inventory.json").write_text(
        json.dumps({"units": []}), encoding="utf-8",
    )
    (specback / "trace.json").write_text(
        json.dumps({
            "source_units_total": 0, "source_units_covered": 0,
            "source_units_excluded": 0, "source_units_uncovered": 0,
        }),
        encoding="utf-8",
    )
    (specback / "goal.json").write_text(
        json.dumps({"template": "default"}), encoding="utf-8",
    )
    (final / "01-overview.md").write_text(
        "# Overview\n\nText.\n", encoding="utf-8",
    )
    for f in cov.REQUIRED_FILES:
        (final / f).write_text(f"# {f}\ncontent\n", encoding="utf-8")
    return specback


def test_build_report_success_path(tmp_path):
    """build_report returns a CoverageReport with sane defaults on a minimal project."""
    specback = _minimal_specback(tmp_path)
    report = cov.build_report(
        specback,
        output_dir=specback,
        target_dir_name="final",
        min_inventory=0,
        max_macro_ratio=1.0,
        min_questions=0,
        max_open_ratio=1.0,
        min_covered_by_fill=0.0,
        min_refs_per_chapter=0,
        min_lines_per_chapter=0,
        min_code_blocks_per_chapter=0,
        min_mermaid_per_chapter=0,
        min_mece_coverage=0.0,
    )
    assert isinstance(report, cov.CoverageReport)
    assert report.total_inventory == 0
    assert report.drafts_scanned >= 1
    assert report.missing_required == []


def test_build_report_gate_failures_when_uncovered(tmp_path):
    """With min_refs raised, chapter metrics report gate failures."""
    specback = _minimal_specback(tmp_path)
    report = cov.build_report(
        specback,
        output_dir=specback,
        target_dir_name="final",
        min_inventory=0,
        max_macro_ratio=1.0,
        min_questions=0,
        max_open_ratio=1.0,
        min_covered_by_fill=0.0,
        min_refs_per_chapter=10,   # chapter has 0 REFs → gate failure
        min_lines_per_chapter=0,
        min_code_blocks_per_chapter=0,
        min_mermaid_per_chapter=0,
        min_mece_coverage=0.0,
    )
    chapter = next(m for m in report.chapter_metrics if m.file == "01-overview.md")
    assert chapter.failures, "expected REF-count gate failure with min_refs=10"


# ---------------------------------------------------------------------------
# Issue #284: split build_report into single-responsibility helpers
# ---------------------------------------------------------------------------


def test_load_goal_json(tmp_path):
    """load_goal_json returns None for missing/invalid/non-dict, dict otherwise."""
    assert cov.load_goal_json(tmp_path) is None
    (tmp_path / "goal.json").write_text("{ bad", encoding="utf-8")
    assert cov.load_goal_json(tmp_path) is None
    (tmp_path / "goal.json").write_text("[1, 2]", encoding="utf-8")
    assert cov.load_goal_json(tmp_path) is None
    (tmp_path / "goal.json").write_text(
        json.dumps({"template": "library-sdk", "depth_mode": "outline"}),
        encoding="utf-8",
    )
    data = cov.load_goal_json(tmp_path)
    assert data == {"template": "library-sdk", "depth_mode": "outline"}


def test_iter_fence_state_tracks_open_close():
    """Fence state toggles on ``` markers; openings are detectable."""
    content = "prose\n```python\ncode\n```\nprose2\n"
    states = list(cov.iter_fence_state(content))
    assert [s[1] for s in states] == [False, False, True, True, False]
    # opening fence = is_fence and not in_code
    assert states[1][2] is True and states[1][1] is False   # opening ```python
    assert states[3][2] is True and states[3][1] is True    # closing ```
    # code line sits inside the fence
    assert states[2][1] is True and states[2][2] is False


def test_count_refs_both_forms():
    assert cov.count_refs("<!-- REF: src/a.py:1-5 --> and <!-- REF: SRC-0042 -->") == 2
    assert cov.count_refs("no refs here") == 0


def test_resolve_target_dir(tmp_path):
    output = tmp_path / "out"
    (output / "final").mkdir(parents=True)
    assert cov.resolve_target_dir(output, "final") == output / "final"
    # fallback to standalone path when output_dir / name is missing
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    assert cov.resolve_target_dir(output, str(standalone)) == standalone
    # no fallback when neither exists
    assert cov.resolve_target_dir(output, "nope") == output / "nope"


def test_compute_mention_coverage_fills_and_returns_uncovered():
    item = cov.InventoryItem(id="INV-1", type="service", name="UserSvc", file="svc.rb", line=1)
    chapters = {"01.md": "UserSvc is mentioned"}
    uncovered = cov.compute_mention_coverage([item], chapters)
    assert item.covered_by == ["01.md"]
    assert uncovered == []
    item2 = cov.InventoryItem(id="INV-2", type="service", name="Missing", file="x.rb", line=1)
    uncovered = cov.compute_mention_coverage([item2], chapters)
    assert uncovered == [item2]
    # pre-filled covered_by is respected
    item3 = cov.InventoryItem(
        id="INV-3", type="service", name="Missing", file="x.rb", line=1,
        covered_by=["01.md"],
    )
    assert cov.compute_mention_coverage([item3], chapters) == []


def test_compute_required_min_inventory(tmp_path):
    assert cov.compute_required_min_inventory(7, tmp_path) == 7
    assert cov.compute_required_min_inventory("auto", tmp_path) == 50  # no source-map
    (tmp_path / "source-map.json").write_text(
        json.dumps({"stats": {"files_scanned": 1000}}), encoding="utf-8",
    )
    assert cov.compute_required_min_inventory("auto", tmp_path) == 50  # 1000//20 = 50


def test_compute_macro_stats():
    items = [
        cov.InventoryItem(id=f"INV-{i}", type=("controller_group" if i % 2 else "service"), name=f"n{i}", file="x", line=1)
        for i in range(4)
    ]
    count, ratio = cov.compute_macro_stats(items)
    assert count == 2
    assert ratio == 0.5
    assert cov.compute_macro_stats([]) == (0, 0.0)


def test_compute_covered_by_fill_rate():
    items = [
        cov.InventoryItem(id="INV-1", type="t", name="a", file="x", line=1, covered_by=["01.md"]),
        cov.InventoryItem(id="INV-2", type="t", name="b", file="x", line=1),
    ]
    assert cov.compute_covered_by_fill_rate(items) == 0.5
    assert cov.compute_covered_by_fill_rate([]) == 0.0


def test_compute_open_question_stats():
    questions = [
        {"status": "open"}, {"status": "open"}, {"status": "answered"},
    ]
    open_q, ratio = cov.compute_open_question_stats(questions)
    assert open_q == 2
    assert ratio == pytest.approx(2 / 3)
    assert cov.compute_open_question_stats([]) == (0, 0.0)


def test_compute_mece_stats():
    trace = {
        "source_units_total": 10, "source_units_covered": 7,
        "source_units_excluded": 2, "source_units_uncovered": 3,
    }
    stats = cov.compute_mece_stats(trace)
    assert isinstance(stats, cov.MeceStats)
    assert stats.total == 10
    assert stats.coverage_rate == pytest.approx(7 / 8)  # denom = 10 - 2
    assert stats.passed_strict is False
    assert cov.compute_mece_stats(None) is None


def test_evaluate_gates_failures():
    failures = cov.evaluate_gates(
        inventory_count=3, required_min=10, macro_ratio=0.5, max_macro_ratio=0.2,
        macro_count=2, covered_by_fill_rate=0.1, min_covered_by_fill=0.9,
        questions_count=1, min_questions=10, open_ratio=0.5, max_open_ratio=0.2,
        mece=cov.MeceStats(coverage_rate=0.3), min_mece_coverage=0.7,
    )
    assert any("inventory.json size" in f for f in failures)
    assert any("macro-type" in f for f in failures)
    assert any("covered_by fill rate" in f for f in failures)
    assert any("questions.json size" in f for f in failures)
    assert any("open-status ratio" in f for f in failures)
    assert any("MECE coverage" in f for f in failures)


def test_evaluate_gates_trace_missing():
    failures = cov.evaluate_gates(
        inventory_count=0, required_min=0, macro_ratio=0.0, max_macro_ratio=1.0,
        macro_count=0, covered_by_fill_rate=1.0, min_covered_by_fill=0.0,
        questions_count=0, min_questions=0, open_ratio=0.0, max_open_ratio=1.0,
        mece=None, min_mece_coverage=0.0,
    )
    assert any("trace.json missing" in f for f in failures)


def test_evaluate_gates_passes_when_within_thresholds():
    failures = cov.evaluate_gates(
        inventory_count=10, required_min=5, macro_ratio=0.1, max_macro_ratio=0.2,
        macro_count=1, covered_by_fill_rate=0.95, min_covered_by_fill=0.9,
        questions_count=10, min_questions=10, open_ratio=0.1, max_open_ratio=0.2,
        mece=cov.MeceStats(total=10, covered=8, excluded=0, uncovered=2, coverage_rate=0.8),
        min_mece_coverage=0.7,
    )
    assert failures == []


def test_check_source_map_refs():
    item = cov.InventoryItem(
        id="INV-1", type="t", name="a", file="x", line=1,
        related_source_ids=["SRC-0001", "SRC-9999"],
    )
    failures = cov.check_source_map_refs([item], {"SRC-0001"})
    assert len(failures) == 1
    assert "SRC-9999" in failures[0]
    assert cov.check_source_map_refs([item], {"SRC-0001", "SRC-9999"}) == []


def test_count_confidence_labels():
    chapters = {"01.md": "🟢 VERIFIED x2 🟡 INFERRED 🔴 ASSUMED VERIFIED"}
    verified, inferred, assumed = cov.count_confidence_labels(chapters)
    assert verified == 3  # 🟢 (1) + "VERIFIED" (2)
    assert inferred == 2  # 🟡 (1) + "INFERRED" (1)
    assert assumed == 2  # 🔴 (1) + "ASSUMED" (1)
    assert cov.count_confidence_labels({}) == (0, 0, 0)


def test_count_confidence_labels_ignores_negated_words():
    # UNVERIFIED / UNASSUMED / DISINFERRED must NOT count as real labels.
    chapters = {
        "01.md": (
            "UNVERIFIED claim (not a label); still UNVERIFIED twice\n"
            "UNASSUMED and DISINFERRED wording\n"
        ),
        "02.md": "just mentioning UNVERIFIED in prose",
    }
    verified, inferred, assumed = cov.count_confidence_labels(chapters)
    assert verified == 0
    assert inferred == 0
    assert assumed == 0


def test_count_confidence_labels_word_boundary():
    chapters = {"01.md": "VERIFIED and re-VERIFIED, but not UNVERIFIED"}
    verified, inferred, assumed = cov.count_confidence_labels(chapters)
    assert verified == 2
    assert inferred == 0
    assert assumed == 0


def test_count_confidence_labels_comment_form():
    # #360: the HTML-comment form (<!-- CONFIDENCE: HIGH | MED | LOW -->) in the
    # phase docs must count alongside the emoji+word form. Also handle the
    # legacy lower-case spellings (verified / inferred / assumed).
    chapters = {
        "01.md": (
            "<!-- CONFIDENCE: HIGH --> statement reliable\n"
            "<!-- CONFIDENCE: MED --> two interpretations\n"
            "<!-- CONFIDENCE: LOW --> needs review\n"
        ),
        "02.md": "<!-- CONFIDENCE: LOW — em-dash description continues -->",
    }
    verified, inferred, assumed = cov.count_confidence_labels(chapters)
    assert verified == 1  # HIGH
    assert inferred == 1  # MED
    assert assumed == 2  # LOW in 01.md + LOW in 02.md (em-dash form)


def test_count_confidence_labels_comment_form_lowercase_legacy():
    # The legacy lower-case comment spellings (specback-health originally) must
    # be counted too, so both forms resolve.
    chapters = {"01.md": "<!-- CONFIDENCE: verified --> <!-- CONFIDENCE: assumed -->"}
    verified, inferred, assumed = cov.count_confidence_labels(chapters)
    assert verified == 1
    assert inferred == 0
    assert assumed == 1


def test_parse_args_defaults_and_overrides():
    args = cov.parse_args(["--min-inventory", "5"])
    assert args.min_inventory == "5"
    assert args.output_format == "text"
    args2 = cov.parse_args(["--output-format", "json", "--strict"])
    assert args2.output_format == "json"
    assert args2.strict is True
