#!/usr/bin/env python3
"""Tests for validate-schema.py — JSON Schema validation."""

import json
import os
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_DIR = SCRIPT_DIR.parent / "schemas"
VALIDATE_SCRIPT = SCRIPT_DIR / "validate-schema.py"
SPECBACK_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / ".specback"

# Load the script as a module for unit tests (stdlib only, no extra imports).
_spec = importlib.util.spec_from_file_location("validate_schema_core", VALIDATE_SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_schema_core"] = mod  # register before exec_module
_spec.loader.exec_module(mod)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _run_validate(schema_path: str, data_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT),
         "--schema", schema_path,
         "--data-file", data_path],
        capture_output=True, text=True,
        cwd=SCRIPT_DIR,
    )


# ---------------------------------------------------------------------------
# Schema structural checks
# ---------------------------------------------------------------------------


class TestGoalSchema:
    def test_goal_schema_is_valid_json(self):
        schema = _schema("goal.schema.json")
        assert schema["title"] == "goal.json"
        assert "properties" in schema
        assert "required" in schema

    def test_goal_schema_has_required_fields(self):
        schema = _schema("goal.schema.json")
        required = set(schema.get("required", []))
        for field in ["output_language", "primary_reader", "granularity",
                      "perspectives", "existing_docs"]:
            assert field in required, f"goal.schema missing required: {field}"

    def test_goal_schema_enums_are_complete(self):
        schema = _schema("goal.schema.json")
        output_lang = schema["properties"]["output_language"]
        assert set(output_lang["enum"]) == {"en", "ja"}
        readers = schema["properties"]["primary_reader"]
        for reader in ["maintenance_developer", "delivery_customer", "sme", "regulator"]:
            assert reader in readers["enum"], f"missing reader: {reader}"


class TestStateSchema:
    def test_state_schema_is_valid_json(self):
        schema = _schema("state.schema.json")
        assert schema["title"] == "state.json"
        assert "phase_progress" in schema["properties"]

    def test_state_schema_has_required_fields(self):
        schema = _schema("state.schema.json")
        required = set(schema.get("required", []))
        for field in ["current_phase", "phase_progress", "started_at", "last_updated"]:
            assert field in required, f"state.schema missing required: {field}"

    def test_state_schema_phase_progress_shape(self):
        schema = _schema("state.schema.json")
        pp = schema["properties"]["phase_progress"]
        assert pp["type"] == "object"
        assert "patternProperties" in pp


class TestQuestionsSchema:
    def test_questions_schema_is_valid_json(self):
        schema = _schema("questions.schema.json")
        assert schema["title"] == "questions.json"
        assert schema["type"] == "array"

    def test_questions_schema_has_required_fields(self):
        schema = _schema("questions.schema.json")
        item_required = set(schema["items"]["required"])
        for field in ["id", "body", "severity", "status"]:
            assert field in item_required, f"questions.schema item missing required: {field}"

    def test_questions_schema_status_enum(self):
        schema = _schema("questions.schema.json")
        status = schema["items"]["properties"]["status"]
        assert set(status["enum"]) == {"open", "answered", "abandoned", "skipped"}


# ---------------------------------------------------------------------------
# Validation against actual .specback/ data
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SPECBACK_DIR.exists(),
    reason=".specback/ directory not found (run from repo root)"
)
class TestLiveData:
    def test_goal_json_validates(self):
        goal_path = SPECBACK_DIR / "goal.json"
        if not goal_path.exists():
            pytest.skip("goal.json not found")
        result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), str(goal_path))
        assert result.returncode == 0, f"goal.json validation failed:\n{result.stderr}"

    def test_state_json_validates(self):
        state_path = SPECBACK_DIR / "state.json"
        if not state_path.exists():
            pytest.skip("state.json not found")
        result = _run_validate(str(SCHEMA_DIR / "state.schema.json"), str(state_path))
        assert result.returncode == 0, f"state.json validation failed:\n{result.stderr}"

    def test_questions_json_validates(self):
        questions_path = SPECBACK_DIR / "questions.json"
        if not questions_path.exists():
            pytest.skip("questions.json not found")
        result = _run_validate(str(SCHEMA_DIR / "questions.schema.json"), str(questions_path))
        assert result.returncode == 0, f"questions.json validation failed:\n{result.stderr}"


# ---------------------------------------------------------------------------
# Error detection
# ---------------------------------------------------------------------------


class TestErrorDetection:
    def test_rejects_invalid_enum(self):
        """A wrong enum value should be caught."""
        bad = {"output_language": "fr", "output_dir": ".", "primary_reader": "maintenance_developer",
               "reader_action": "code_change", "granularity": "medium",
               "perspectives": ["functional_correctness"], "existing_docs": "none"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), tmp)
            assert result.returncode == 1
            assert "fr" in result.stderr
        finally:
            os.unlink(tmp)

    def test_rejects_missing_required(self):
        """Missing required field should be caught."""
        bad = {"output_language": "en"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), tmp)
            assert result.returncode == 1
            assert "missing required" in result.stderr
        finally:
            os.unlink(tmp)

    def test_rejects_invalid_json(self):
        """Malformed JSON should be caught."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid}")
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), tmp)
            assert result.returncode == 2
            assert "Invalid data JSON" in result.stderr or "Invalid" in result.stderr
        finally:
            os.unlink(tmp)

    def test_rejects_nonexistent_file(self):
        """Missing data file should be caught."""
        result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), "/nonexistent/foo.json")
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_rejects_extra_property(self):
        """additionalProperties: false should reject unknown keys."""
        bad = {"output_language": "en", "output_dir": ".", "primary_reader": "maintenance_developer",
               "reader_action": "code_change", "granularity": "medium",
               "perspectives": ["functional_correctness"], "existing_docs": "none",
               "bogus_field": "should fail"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), tmp)
            assert result.returncode == 1
            assert "unexpected property" in result.stderr
        finally:
            os.unlink(tmp)

    def test_rejects_bad_question_status(self):
        """Invalid question status should be caught."""
        bad = [{"id": "Q-001", "generated_at_phase": "setup", "category": "architecture_decision",
                "body": "test", "severity": "important", "status": "invalid_status"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "questions.schema.json"), tmp)
            assert result.returncode == 1
            assert "invalid_status" in result.stderr
        finally:
            os.unlink(tmp)

    def test_accepts_valid_goal(self):
        """A well-formed goal.json should pass."""
        good = {"output_language": "en", "output_dir": "specs", "primary_reader": "maintenance_developer",
                "reader_action": "code_change", "granularity": "medium",
                "perspectives": ["functional_correctness", "operability"],
                "existing_docs": "none", "free_text_notes": "", "user_custom_deliverables": [],
                "depth_mode": "comprehensive"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(good, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "goal.schema.json"), tmp)
            assert result.returncode == 0, f"valid goal rejected:\n{result.stderr}"
        finally:
            os.unlink(tmp)

    def test_accepts_valid_state(self):
        """A well-formed state.json should pass."""
        good = {"current_phase": 3, "phase_progress": {
                    "phase_0": {"total_subtasks": 5, "completed_subtasks": 5, "blocked_subtasks": []},
                    "phase_1": {"total_subtasks": 4, "completed_subtasks": 4, "blocked_subtasks": []}},
                "started_at": "2026-07-30T00:00:00+09:00",
                "last_updated": "2026-07-30T12:00:00+09:00",
                "all_quality_gates_passed": False,
                "session_history": [{"timestamp": "2026-07-30T00:00:00+09:00", "phase": 0, "event": "started"}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(good, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "state.schema.json"), tmp)
            assert result.returncode == 0, f"valid state rejected:\n{result.stderr}"
        finally:
            os.unlink(tmp)

    def test_accepts_valid_questions(self):
        """A well-formed questions.json should pass."""
        good = [{"id": "Q-001", "generated_at_phase": "investigation",
                 "category": "architecture_decision", "body": "Test question?",
                 "severity": "important", "status": "open"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(good, f)
            tmp = f.name
        try:
            result = _run_validate(str(SCHEMA_DIR / "questions.schema.json"), tmp)
            assert result.returncode == 0, f"valid questions rejected:\n{result.stderr}"
        finally:
            os.unlink(tmp)


def test_main_returns_int_for_missing_schema() -> None:
    """main(argv) returns an int exit code instead of None (Issue #286)."""
    rc = mod.main(["--schema", "no-such-schema.json", "--data-file", "no-such-data.json"])
    assert rc == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
