"""Tests for specback-incremental-update.py (Issue #269).

Covers: plan (affected-chapter identification, prompt generation, state
snapshot), verify (SRC-ID renumbering trap guard, zero collateral edits),
apply (backup + atomic replace + trace refresh), and the CLI exit-code
contract (0/1/2/3).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "specback-incremental-update.py"

# Import the script as a module for pure-function tests (repo pattern).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_spec = importlib.util.spec_from_file_location("specback_incremental_update_core", SCRIPT)
assert _spec is not None and _spec.loader is not None
incr = importlib.util.module_from_spec(_spec)
sys.modules["specback_incremental_update_core"] = incr
_spec.loader.exec_module(incr)


def _run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True, text=True, cwd=cwd, timeout=60,
    )


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_fixture(root: Path) -> dict:
    """Build a minimal specback dir + output dir with one affected chapter."""
    specback = root / ".specback"
    specback.mkdir(parents=True, exist_ok=True)
    output = root / "specs"
    output.mkdir(parents=True, exist_ok=True)

    _write_json(specback / "wbs.json", {
        "template": "web-app",
        "chapters": [
            {"filename": "00-metadata.md", "title": "Metadata", "kind": "reserved"},
            {"filename": "05-data-model.md", "title": "Data model", "kind": "standard"},
        ],
    })
    _write_json(specback / "source-map.json", {
        "schema_version": "0.1.0",
        "units": [
            {"id": "SRC-0010", "path": "src/models/issue.rb",
             "line_range": [1, 40], "kind": "class", "name": "Issue"},
            {"id": "SRC-0020", "path": "src/models/user.rb",
             "line_range": [1, 30], "kind": "class", "name": "User"},
        ],
    })
    _write_json(specback / "trace.json", {
        "schema_version": "0.2.0",
        "by_source": {
            "SRC-0010": {
                "path": "src/models/issue.rb",
                "covered_by_sections": [
                    {"file": "05-data-model.md", "section": "5.2 Issue"},
                ],
            },
        },
    })
    _write_json(specback / "drift-report.json", {
        "schema_version": "0.1.0",
        "generated_at": "2026-08-11T00:00:00Z",
        "base": "HEAD",
        "summary": {"affected_spec_sections": 1, "changed_files": 1},
        "changes": [
            {
                "file": "src/models/issue.rb",
                "status": "M",
                "src_ids": ["SRC-0010"],
                "impacted_sections": [
                    {"file": "05-data-model.md", "section": "5.2 Issue",
                     "impact": "moderate"},
                ],
            },
        ],
        "deleted_with_refs": [],
        "new_uncovered": [],
        "no_impact": [],
    })
    (output / "05-data-model.md").write_text(
        "# Data model\n\n## 5.2 Issue\n\nIssues are stored. <!-- REF: SRC-0010 -->\n",
        encoding="utf-8",
    )
    (output / "00-metadata.md").write_text(
        "# Metadata\n\nGenerated. <!-- REF: SRC-0020 -->\n", encoding="utf-8",
    )
    return {"specback": specback, "output": output}


# ---------------------------------------------------------------------------
# help / CLI surface
# ---------------------------------------------------------------------------

def test_help_shows_subcommands():
    result = _run("--help")
    assert result.returncode == 0
    for name in ("plan", "verify", "apply"):
        assert name in result.stdout


def test_plan_help():
    result = _run("plan", "--help")
    assert result.returncode == 0
    assert "--specback-dir" in result.stdout
    assert "--output-dir" in result.stdout
    assert "--drift-report" in result.stdout
    assert "--json" in result.stdout


def test_verify_help_includes_updated():
    result = _run("verify", "--help")
    assert result.returncode == 0
    assert "--updated" in result.stdout


def test_apply_help_includes_skip_trace():
    result = _run("apply", "--help")
    assert result.returncode == 0
    assert "--skip-trace-refresh" in result.stdout


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def test_plan_writes_prompt_and_state(tmp_path):
    fx = _make_fixture(tmp_path)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--json",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == "0.1.0"
    assert [c["file"] for c in data["affected_chapters"]] == ["05-data-model.md"]
    assert data["affected_chapters"][0]["title"] == "Data model"
    assert data["affected_chapters"][0]["changed_sources"] == ["src/models/issue.rb"]

    prompts_dir = fx["specback"] / "incremental" / "prompts"
    prompt = prompts_dir / "05-data-model.md"
    assert prompt.exists()
    text = prompt.read_text(encoding="utf-8")
    assert "Incremental re-investigation: 05-data-model.md" in text
    assert "src/models/issue.rb" in text
    assert "SRC-0010" in text

    state = json.loads((fx["specback"] / "incremental" / "state.json").read_text())
    assert state["affected_chapters"][0]["file"] == "05-data-model.md"
    assert isinstance(state["chapter_hashes"]["05-data-model.md"], str)
    assert isinstance(state["chapter_hashes"]["00-metadata.md"], str)


def test_drift_report_resolution_output_dir_preferred(tmp_path):
    """Canonical {output-dir}/drift-report.json wins over {specback-dir} (SB-02)."""
    root = tmp_path / "proj"
    specback = root / ".specback"
    output = root / "specs"
    specback.mkdir(parents=True)
    output.mkdir(parents=True)
    # Distinct reports: output-dir holds the canonical report.
    _write_json(specback / "drift-report.json", {
        "changes": [{"file": "05-data-model.md", "status": "M", "impacted_sections": []}],
        "deleted_with_refs": [], "no_impact": [], "new_uncovered": [],
    })
    _write_json(output / "drift-report.json", {
        "changes": [{"file": "00-metadata.md", "status": "M", "impacted_sections": []}],
        "deleted_with_refs": [], "no_impact": [], "new_uncovered": [],
    })
    _write_json(specback / "wbs.json", {"chapters": [
        {"filename": "00-metadata.md", "title": "Metadata", "kind": "reserved"},
        {"filename": "05-data-model.md", "title": "Data model", "kind": "standard"},
    ]})
    _write_json(specback / "source-map.json", {"units": []})
    _write_json(specback / "trace.json", {"by_source": {}})
    result = _run("plan", "--specback-dir", str(specback),
                  "--output-dir", str(output), "--json")
    assert result.returncode == 2, result.stderr  # no affected @ 00-metadata (reserved)
    state = json.loads((specback / "incremental" / "state.json").read_text())
    assert state["affected_chapters"] == []


def test_drift_report_resolution_specback_fallback(tmp_path):
    """No canonical report → fall back to {specback-dir}/drift-report.json (SB-02)."""
    root = tmp_path / "proj"
    specback = root / ".specback"
    output = root / "specs"
    specback.mkdir(parents=True)
    output.mkdir(parents=True)
    # Only specback has the report (legacy layout); it lists a real affected chapter.
    _write_json(specback / "drift-report.json", {
        "changes": [{"file": "src/models/issue.rb", "status": "M",
                     "impacted_sections": [{"file": "05-data-model.md",
                                            "section": "5.2 Issue"}]}],
        "deleted_with_refs": [], "no_impact": [], "new_uncovered": [],
    })
    _write_json(specback / "wbs.json", {"chapters": [
        {"filename": "05-data-model.md", "title": "Data model", "kind": "standard"},
    ]})
    _write_json(specback / "source-map.json", {
        "units": [{"id": "SRC-0010", "path": "src/models/issue.rb",
                   "line_range": [1, 40], "kind": "class", "name": "Issue"}],
    })
    _write_json(specback / "trace.json", {"by_source": {}})
    result = _run("plan", "--specback-dir", str(specback),
                  "--output-dir", str(output), "--json")
    # If the fallback had failed, plan would exit 1 (missing drift report).
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [c["file"] for c in data["affected_chapters"]] == ["05-data-model.md"]


def test_plan_missing_drift_report_exit_1(tmp_path):
    specback = tmp_path / ".specback"
    specback.mkdir(parents=True)
    _write_json(specback / "wbs.json", {"chapters": []})
    _write_json(specback / "source-map.json", {"units": []})
    _write_json(specback / "trace.json", {"by_source": {}})
    result = _run("plan", "--specback-dir", str(specback))
    assert result.returncode == 1
    assert "drift-report" in result.stderr


def test_plan_no_affected_chapters_exit_2(tmp_path):
    specback = tmp_path / ".specback"
    specback.mkdir(parents=True)
    _write_json(specback / "wbs.json", {"chapters": []})
    _write_json(specback / "source-map.json", {"units": []})
    _write_json(specback / "trace.json", {"by_source": {}})
    _write_json(specback / "drift-report.json", {
        "changes": [], "deleted_with_refs": [], "new_uncovered": [], "no_impact": [],
    })
    result = _run("plan", "--specback-dir", str(specback))
    assert result.returncode == 2
    state_path = specback / "incremental" / "state.json"
    assert state_path.exists()
    assert json.loads(state_path.read_text())["affected_chapters"] == []


def test_plan_non_dict_json_exit_1(tmp_path):
    specback = tmp_path / ".specback"
    specback.mkdir(parents=True)
    (specback / "drift-report.json").write_text("[]", encoding="utf-8")
    _write_json(specback / "wbs.json", {"chapters": []})
    _write_json(specback / "source-map.json", {"units": []})
    _write_json(specback / "trace.json", {"by_source": {}})
    result = _run("plan", "--specback-dir", str(specback))
    assert result.returncode == 1
    assert "not a JSON object" in result.stderr


def test_plan_oversized_input_exit_1(tmp_path, monkeypatch):
    fx = _make_fixture(tmp_path)
    # Monkeypatching only affects the in-process module, so exercise the guard
    # via the module function directly (the CLI runs in a fresh subprocess and
    # would not see the monkeypatch).
    monkeypatch.setattr(incr, "MAX_INPUT_BYTES", 10)  # force tiny cap
    try:
        incr._load_json_object(fx["specback"] / "wbs.json", "wbs.json")
        assert False, "expected _fail (SystemExit) for oversized input"
    except SystemExit as exc:
        assert exc.code == 1


def test_plan_deleted_with_refs_included(tmp_path):
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift = json.loads(drift_path.read_text())
    drift["changes"] = []
    drift["deleted_with_refs"] = [{
        "file": "src/models/issue.rb", "status": "D", "src_ids": ["SRC-0010"],
        "impacted_sections": [
            {"file": "05-data-model.md", "section": "5.2 Issue", "impact": "high"},
        ],
    }]
    _write_json(drift_path, drift)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--json",
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert [c["file"] for c in data["affected_chapters"]] == ["05-data-model.md"]


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _plan_first(fx: dict) -> None:
    _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )


def test_verify_missing_state_exit_3(tmp_path):
    fx = _make_fixture(tmp_path)
    updated = fx["output"] / "05-data-model.md"
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
    )
    assert result.returncode == 3
    assert "plan" in result.stderr


def test_verify_target_not_affected_fail(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    # A chapter never marked as affected by plan.
    updated = fx["output"] / "00-metadata.md"
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["target_affected"] is False
    assert data["passed"] is False


def test_verify_missing_src_id_fail(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\n## 5.2 Issue\n\nRenumbered claim. <!-- REF: SRC-9999 -->\n",
        encoding="utf-8",
    )
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["src_id_check"]["missing"] == ["SRC-9999"]
    assert data["src_id_check"]["missing_count"] == 1
    assert data["passed"] is False


def test_verify_mixed_ref_forms_only_src_id_checked(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\n## 5.2 Issue\n\nBoth forms. <!-- REF: SRC-0010 -->\n"
        "Also path form. <!-- REF: src/models/issue.rb:1-40 -->\n",
        encoding="utf-8",
    )
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["src_id_check"]["missing"] == []
    assert data["passed"] is True


def test_verify_pass(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\n## 5.2 Issue\n\nUpdated content. <!-- REF: SRC-0010 -->\n",
        encoding="utf-8",
    )
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["passed"] is True
    assert data["collateral_check"]["changed_unexpected"] == []


def test_verify_collateral_change_fail(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    # Unrelated chapter changes after plan → collateral violation.
    (fx["output"] / "00-metadata.md").write_text(
        "# Metadata\n\nTampered. <!-- REF: SRC-0020 -->\n", encoding="utf-8",
    )
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\n## 5.2 Issue\n\nUpdated content. <!-- REF: SRC-0010 -->\n",
        encoding="utf-8",
    )
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert "00-metadata.md" in data["collateral_check"]["changed_unexpected"]
    assert data["passed"] is False


def test_verify_unchanged_target_warns_but_passes(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated = fx["output"] / "05-data-model.md"  # identical to baseline
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["passed"] is True
    assert data["collateral_check"]["unchanged_target"] is True


def test_verify_updated_missing_file_exit_1(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(fx["output"] / "nope.md"),
    )
    assert result.returncode == 1
    assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def test_apply_aborts_on_check_failure(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    original = (fx["output"] / "05-data-model.md").read_text()
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\nBad REF. <!-- REF: SRC-9999 -->\n", encoding="utf-8",
    )
    result = _run(
        "apply",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
    )
    assert result.returncode == 1
    assert (fx["output"] / "05-data-model.md").read_text() == original
    assert not (fx["output"] / "05-data-model.md.pre-incremental").exists()


def test_apply_success_replaces_and_backs_up(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    new_text = "# Data model\n\n## 5.2 Issue\n\nFully updated. <!-- REF: SRC-0010 -->\n"
    updated.write_text(new_text, encoding="utf-8")
    result = _run(
        "apply",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--skip-trace-refresh",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (fx["output"] / "05-data-model.md").read_text() == new_text
    backup = fx["output"] / "05-data-model.md.pre-incremental"
    assert backup.exists()
    assert "# Data model" in backup.read_text()


def test_apply_json_output(tmp_path):
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\nUpdated. <!-- REF: SRC-0010 -->\n", encoding="utf-8",
    )
    result = _run(
        "apply",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--skip-trace-refresh",
        "--json",
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["applied"] is True
    assert data["target"] == "05-data-model.md"


# ---------------------------------------------------------------------------
# pure-function unit tests
# ---------------------------------------------------------------------------

def test_collect_affected_chapters_dedupes():
    drift = {
        "changes": [{"impacted_sections": [
            {"file": "02-entities.md", "section": "2.1"},
            {"file": "05-data.md", "section": "5.3"},
        ]}],
        "deleted_with_refs": [{"impacted_sections": [
            {"file": "02-entities.md", "section": "2.2"},
        ]}],
    }
    assert incr._collect_affected_chapters(drift) == ["02-entities.md", "05-data.md"]


def test_collect_chapter_src_ids_from_entry():
    drift = {
        "changes": [{
            "file": "a.py", "status": "M", "src_ids": ["SRC-0001", "SRC-0002"],
            "impacted_sections": [{"file": "01-overview.md", "section": "1.1"}],
        }],
        "deleted_with_refs": [],
    }
    trace = {"by_source": {}}
    assert incr._collect_chapter_src_ids(drift, trace, "01-overview.md") == [
        "SRC-0001", "SRC-0002",
    ]


def test_collect_chapter_src_ids_fallback_trace():
    drift = {"changes": [], "deleted_with_refs": []}
    trace = {"by_source": {
        "SRC-0001": {"covered_by_sections": [{"file": "03-api.md", "section": "3.1"}]},
        "SRC-0002": {"covered_by_sections": [{"file": "04-ops.md", "section": "4.1"}]},
    }}
    assert incr._collect_chapter_src_ids(drift, trace, "03-api.md") == ["SRC-0001"]


def test_is_chapter_file_naming():
    assert incr._is_chapter_file("05-data-model.md")
    assert incr._is_chapter_file("00-metadata.md")
    assert incr._is_chapter_file("99-unresolved.md")
    assert incr._is_chapter_file("traceability.md")
    assert not incr._is_chapter_file("README.md")
    assert not incr._is_chapter_file("chapter2_architecture.md")
    assert not incr._is_chapter_file("notes.txt")


def test_src_id_regex():
    text = "a <!-- REF: SRC-0142 --> b <!-- REF: SRC-0003 --> c"
    assert incr.SRC_ID_RE.findall(text) == ["SRC-0142", "SRC-0003"]


def test_src_id_regex_ignores_path_form():
    text = "<!-- REF: src/models/issue.rb:1-40 -->"
    assert incr.SRC_ID_RE.findall(text) == []


def test_render_prompt_contains_required_sections(tmp_path):
    specback = tmp_path / ".specback"
    prompt = incr._render_prompt(
        "05-data-model.md", "Data model",
        {"generated_at": "T", "base": "HEAD"},
        ["src/models/issue.rb"], ["SRC-0010"], specback,
    )
    assert "Changed sources affecting this chapter" in prompt
    assert "src/models/issue.rb" in prompt
    assert "SRC-IDs to re-check" in prompt
    assert "SRC-0010" in prompt
    assert "Do not touch other chapters" in prompt


# ---------------------------------------------------------------------------
# security hardening regression tests (review findings B2/F1-F5, S2)
# ---------------------------------------------------------------------------

def test_plan_rejects_path_traversal_chapter_name(tmp_path):
    """drift-report impacted_sections[].file with ../ must not escape prompts dir."""
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift = json.loads(drift_path.read_text())
    drift["changes"] = [{
        "file": "src/models/issue.rb", "status": "M", "src_ids": ["SRC-0010"],
        "impacted_sections": [
            {"file": "../../../escaped.md", "section": "5.2", "impact": "moderate"},
        ],
    }]
    _write_json(drift_path, drift)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 1
    assert "invalid chapter filename" in result.stderr
    # Nothing written outside the prompts dir (the fail happens before any
    # directory is created, so prompts/ must not exist at all).
    prompts_dir = fx["specback"] / "incremental" / "prompts"
    assert not prompts_dir.exists()
    assert not (tmp_path / "escaped.md").exists()


def test_plan_rejects_absolute_chapter_name(tmp_path):
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift = json.loads(drift_path.read_text())
    drift["changes"] = [{
        "file": "src/models/issue.rb", "status": "M", "src_ids": ["SRC-0010"],
        "impacted_sections": [
            {"file": "/tmp/abs-evil.md", "section": "5.2", "impact": "moderate"},
        ],
    }]
    _write_json(drift_path, drift)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 1
    assert "invalid chapter filename" in result.stderr


def test_plan_rejects_non_list_changes(tmp_path):
    """Malformed drift shape (changes as dict) must fail cleanly, not traceback."""
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift = json.loads(drift_path.read_text())
    drift["changes"] = {"oops": 1}
    _write_json(drift_path, drift)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 1
    assert "must be a list" in result.stderr
    assert "Traceback" not in result.stderr


def test_plan_rejects_nan_constant(tmp_path):
    """NaN in JSON must be rejected (posture match with #273)."""
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift_path.write_text(
        '{"schema_version":"0.1.0","generated_at":NaN,"base":"HEAD",'
        '"changes":[],"deleted_with_refs":[],"new_uncovered":[],"no_impact":[]}',
        encoding="utf-8",
    )
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 1
    assert "non-finite" in result.stderr


def test_state_symlink_write_refused(tmp_path):
    """state.json.tmp symlink must not be followed (O_NOFOLLOW)."""
    fx = _make_fixture(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL", encoding="utf-8")
    incr_dir = fx["specback"] / "incremental"
    incr_dir.mkdir(parents=True, exist_ok=True)
    (incr_dir / "state.json.tmp").symlink_to(victim)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    # The unlink+O_EXCL strategy removes the symlink and creates a regular file,
    # so the victim must be untouched and plan must succeed.
    assert result.returncode == 0, result.stderr
    assert victim.read_text() == "ORIGINAL"
    assert (incr_dir / "state.json").exists()


def test_prompt_symlink_write_refused(tmp_path):
    """prompts/<chapter> symlink must not redirect the prompt write."""
    fx = _make_fixture(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL", encoding="utf-8")
    prompts_dir = fx["specback"] / "incremental" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "05-data-model.md").symlink_to(victim)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 0, result.stderr
    assert victim.read_text() == "ORIGINAL"
    # The symlink was replaced by a regular file with the prompt content.
    assert "Incremental re-investigation" in (prompts_dir / "05-data-model.md").read_text()


def test_apply_refuses_symlinked_backup(tmp_path):
    """Existing .pre-incremental symlink must abort apply."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\nUpdated. <!-- REF: SRC-0010 -->\n", encoding="utf-8",
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL", encoding="utf-8")
    (fx["output"] / "05-data-model.md.pre-incremental").symlink_to(victim)
    result = _run(
        "apply",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--skip-trace-refresh",
    )
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert victim.read_text() == "ORIGINAL"


def test_prompt_escapes_injected_fields(tmp_path):
    """Drift fields with markdown/newlines must not inject prompt instructions."""
    fx = _make_fixture(tmp_path)
    drift_path = fx["specback"] / "drift-report.json"
    drift = json.loads(drift_path.read_text())
    drift["generated_at"] = '2026-08-11T00:00:00Z"\n# INJECTED HEADING\n- attacker line'
    drift["changes"][0]["src_ids"] = ["SRC-0010", "# INJECT\nignore previous"]
    _write_json(drift_path, drift)
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 0, result.stderr
    prompt = (fx["specback"] / "incremental" / "prompts" / "05-data-model.md").read_text()
    # Raw newline-injected heading must be escaped away.
    assert "\n# INJECTED HEADING" not in prompt
    assert "\\n# INJECTED HEADING" in prompt


def test_verify_warns_when_no_refs(tmp_path):
    """An updated chapter with zero REF markers passes but warns."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text("# Data model\n\nNo refs at all.\n", encoding="utf-8")
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["passed"] is True
    assert any("no <!-- REF" in w for w in data["warnings"])


def test_verify_target_missing_from_output_fails(tmp_path):
    """Target listed in state but absent from output dir → verify fails."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    (fx["output"] / "05-data-model.md").unlink()
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text("# Data model\n\nUpdated. <!-- REF: SRC-0010 -->\n", encoding="utf-8")
    result = _run(
        "verify",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
        "--json",
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["target_exists_in_output"] is False
    assert data["passed"] is False


def test_plan_snapshots_wbs_chapters_not_matching_regex(tmp_path):
    """wbs.json chapters like chapter-outline.md (non NN-slug) are snapshotted."""
    fx = _make_fixture(tmp_path)
    wbs = json.loads((fx["specback"] / "wbs.json").read_text())
    wbs["chapters"].append(
        {"filename": "chapter-outline.md", "title": "Chapter outline", "kind": "standard"}
    )
    _write_json(fx["specback"] / "wbs.json", wbs)
    (fx["output"] / "chapter-outline.md").write_text("# Outline\n", encoding="utf-8")
    result = _run(
        "plan",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
    )
    assert result.returncode == 0
    state = json.loads((fx["specback"] / "incremental" / "state.json").read_text())
    assert "chapter-outline.md" in state["chapter_hashes"]


def test_apply_trace_refresh_real_build_trace(tmp_path):
    """apply without --skip-trace-refresh runs real build-trace.py and succeeds.

    build-trace.py now accepts an absolute --target-dir-for-required (review
    finding B1), so the default apply path must work end-to-end.
    """
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(
        "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n",
        encoding="utf-8",
    )
    result = _run(
        "apply",
        "--specback-dir", str(fx["specback"]),
        "--output-dir", str(fx["output"]),
        "--updated", str(updated),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (fx["specback"] / "trace.json").exists()
    trace = json.loads((fx["specback"] / "trace.json").read_text())
    assert "by_source" in trace


def test_plan_specback_dir_parses_as_path(tmp_path):
    """--specback-dir is parsed as a Path (common helper, F2)."""
    import argparse
    from common import add_specback_dir_arg
    parser = argparse.ArgumentParser()
    add_specback_dir_arg(parser)
    args = parser.parse_args(["--specback-dir", "custom-sb"])
    assert args.specback_dir == Path("custom-sb")
    args_default = parser.parse_args([])
    assert args_default.specback_dir == Path(".specback")


# ---------------------------------------------------------------------------
# SB-03 / Issue #374 — rollback on apply trace-refresh failure
# ---------------------------------------------------------------------------

def _apply_in_process(fx: dict, new_text: str,
                      monkeypatch, trace_failure: bool = True) -> dict:
    """Run cmd_apply in-process with trace refresh simulated (or skipped)."""
    import argparse
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "05-data-model.md"
    updated.write_text(new_text, encoding="utf-8")

    args = argparse.Namespace(
        specback_dir=str(fx["specback"]),
        output_dir=str(fx["output"]),
        updated=str(updated),
        json=True,
        skip_trace_refresh=False,
    )
    if trace_failure:
        fake = subprocess.CompletedProcess(
            args=["build-trace.py"], returncode=1,
            stdout="", stderr="boom: trace build failed",
        )
        monkeypatch.setattr(incr, "subprocess", _FakeSubprocess(fake))

    import io
    stdout = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, io.StringIO()
    try:
        rc = incr.cmd_apply(args)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return {"rc": rc, "json": json.loads(stdout.getvalue())}


class _FakeSubprocess:
    """Monkeypatched subprocess stub that returns a fixed CompletedProcess."""
    def __init__(self, result: subprocess.CompletedProcess) -> None:
        self._result = result

    def run(self, *args, **kwargs):
        return self._result


def test_apply_trace_failure_rolls_back_chapter_and_trace(tmp_path, monkeypatch):
    """SB-03: build-trace failure rolls back chapter + trace to pre-apply state."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    original_chapter = (fx["output"] / "05-data-model.md").read_text()
    original_trace = (fx["specback"] / "trace.json").read_text()

    new_text = "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n"
    out = _apply_in_process(fx, new_text, monkeypatch, trace_failure=True)

    assert out["rc"] == 1
    data = out["json"]
    assert data["applied"] is False
    assert "build-trace.py failed" in data["reason"]
    assert data["rollback"]["chapter_restored"] is True
    assert data["rollback"]["trace_restored"] is True
    # Chapter content restored to original.
    assert (fx["output"] / "05-data-model.md").read_text() == original_chapter
    # Trace restored to original.
    assert (fx["specback"] / "trace.json").read_text() == original_trace


def test_apply_trace_failure_rollback_json_hashes_match(tmp_path, monkeypatch):
    """Acceptance: apply non-zero exit → chapter & trace hashes match pre-apply."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    orig_ch_hash = incr.sha256_file(fx["output"] / "05-data-model.md")
    orig_tr_hash = incr.sha256_file(fx["specback"] / "trace.json")

    new_text = "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n"
    out = _apply_in_process(fx, new_text, monkeypatch, trace_failure=True)
    assert out["rc"] == 1
    assert incr.sha256_file(fx["output"] / "05-data-model.md") == orig_ch_hash
    assert incr.sha256_file(fx["specback"] / "trace.json") == orig_tr_hash


def test_apply_trace_failure_when_no_prior_trace(tmp_path, monkeypatch):
    """SB-03: if trace.json did not exist before apply, a failure removes it."""
    fx = _make_fixture(tmp_path)
    _plan_first(fx)
    # Simulate a project with no trace.json yet (plan tolerates it via fallback,
    # but here we delete it to exercise the "no original" rollback branch).
    (fx["specback"] / "trace.json").unlink()
    orig_chapter = (fx["output"] / "05-data-model.md").read_text()

    new_text = "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n"
    out = _apply_in_process(fx, new_text, monkeypatch, trace_failure=True)

    assert out["rc"] == 1
    assert out["json"]["rollback"]["chapter_restored"] is True
    assert out["json"]["rollback"]["trace_restored"] is True
    assert (fx["output"] / "05-data-model.md").read_text() == orig_chapter
    assert not (fx["specback"] / "trace.json").exists()


# ---------------------------------------------------------------------------
# SB-04 / Issue #375 — baseline refresh on apply, multi-chapter sequential apply
# ---------------------------------------------------------------------------

def _make_two_chapter_fixture(root: Path) -> dict:
    """Build a fixture with TWO affected chapters (05-data-model + 03-api)."""
    fx = _make_fixture(root)
    specback, output = fx["specback"], fx["output"]

    # Second source unit + chapter.
    sm = json.loads((specback / "source-map.json").read_text())
    sm["units"].append({
        "id": "SRC-0030", "path": "src/api/server.rb",
        "line_range": [1, 50], "kind": "class", "name": "Server",
    })
    (specback / "source-map.json").write_text(json.dumps(sm), encoding="utf-8")

    tr = json.loads((specback / "trace.json").read_text())
    tr["by_source"]["SRC-0030"] = {
        "path": "src/api/server.rb",
        "covered_by_sections": [{"file": "03-api.md", "section": "3.1 Server"}],
    }
    (specback / "trace.json").write_text(json.dumps(tr), encoding="utf-8")

    dr = json.loads((specback / "drift-report.json").read_text())
    dr["changes"].append({
        "file": "src/api/server.rb", "status": "M", "src_ids": ["SRC-0030"],
        "impacted_sections": [{
            "file": "03-api.md", "section": "3.1 Server", "impact": "moderate",
        }],
    })
    (specback / "drift-report.json").write_text(json.dumps(dr), encoding="utf-8")

    wbs = json.loads((specback / "wbs.json").read_text())
    wbs["chapters"].append({"filename": "03-api.md", "title": "API", "kind": "standard"})
    (specback / "wbs.json").write_text(json.dumps(wbs), encoding="utf-8")

    (output / "03-api.md").write_text(
        "# API\n\n## 3.1 Server\n\nServer ref. <!-- REF: SRC-0030 -->\n",
        encoding="utf-8",
    )
    return fx


def _apply_ok_in_process(fx: dict, chapter: str, new_text: str) -> bool:
    """Run cmd_apply in-process with trace refresh skipped (SB-04 focus)."""
    import argparse
    import io
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / chapter
    updated.write_text(new_text, encoding="utf-8")
    args = argparse.Namespace(
        specback_dir=str(fx["specback"]),
        output_dir=str(fx["output"]),
        updated=str(updated),
        json=True,
        skip_trace_refresh=True,
    )
    stdout = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, io.StringIO()
    try:
        rc = incr.cmd_apply(args)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return rc == 0


def test_apply_refreshes_baseline_hash(tmp_path):
    """SB-04: after a successful apply, state.json chapter_hashes[target] updates."""
    fx = _make_two_chapter_fixture(tmp_path)
    _plan_first(fx)
    state_path = fx["specback"] / "incremental" / "state.json"
    state = json.loads(state_path.read_text())
    orig_hash = state["chapter_hashes"]["05-data-model.md"]

    new_text = "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n"
    assert _apply_ok_in_process(fx, "05-data-model.md", new_text) is True

    state2 = json.loads(state_path.read_text())
    new_hash = state2["chapter_hashes"]["05-data-model.md"]
    assert new_hash != orig_hash
    assert new_hash == incr.sha256_file(fx["output"] / "05-data-model.md")


def test_multi_chapter_sequential_apply_not_collateral(tmp_path):
    """SB-04: applying chapter 1 then chapter 2 does not flag chapter 1 as collateral."""
    fx = _make_two_chapter_fixture(tmp_path)
    _plan_first(fx)

    # Apply chapter 1 (05-data-model.md) first.
    ch1_new = "# Data model\n\n## 5.2 Issue\n\nUpdated. <!-- REF: SRC-0010 -->\n"
    assert _apply_ok_in_process(fx, "05-data-model.md", ch1_new) is True

    # Verify chapter 2 (03-api.md) — chapter 1's legitimate change must NOT be
    # flagged as a collateral edit (baseline was refreshed on apply).
    updated_dir = fx["specback"] / "incremental" / "updated"
    updated_dir.mkdir(parents=True, exist_ok=True)
    updated = updated_dir / "03-api.md"
    updated.write_text(
        "# API\n\n## 3.1 Server\n\nFully updated. <!-- REF: SRC-0030 -->\n",
        encoding="utf-8",
    )
    result, _ = incr._run_verify_checks(
        Path(fx["specback"]), Path(fx["output"]), updated.read_bytes(), "03-api.md",
    )
    assert result["passed"] is True, result
    assert result["collateral_check"]["changed_unexpected"] == []

    # And chapter 2 can be applied too.
    assert _apply_ok_in_process(fx, "03-api.md",
                                "# API\n\n## 3.1 Server\n\nFully updated. <!-- REF: SRC-0030 -->\n") is True


def test_multi_chapter_apply_smoke(tmp_path):
    """End-to-end: plan + apply all N chapters sequentially without replan."""
    fx = _make_two_chapter_fixture(tmp_path)
    _plan_first(fx)
    _apply_ok_in_process(fx, "05-data-model.md",
                         "# Data model\n\n## 5.2 Issue\n\nUpdated1. <!-- REF: SRC-0010 -->\n")
    _apply_ok_in_process(fx, "03-api.md",
                         "# API\n\n## 3.1 Server\n\nUpdated2. <!-- REF: SRC-0030 -->\n")
    # Both chapters reflect their applied content (baseline stayed consistent).
    assert "Updated1" in (fx["output"] / "05-data-model.md").read_text()
    assert "Updated2" in (fx["output"] / "03-api.md").read_text()
    state = json.loads((fx["specback"] / "incremental" / "state.json").read_text())
    assert state["chapter_hashes"]["05-data-model.md"] == \
        incr.sha256_file(fx["output"] / "05-data-model.md")
    assert state["chapter_hashes"]["03-api.md"] == \
        incr.sha256_file(fx["output"] / "03-api.md")
