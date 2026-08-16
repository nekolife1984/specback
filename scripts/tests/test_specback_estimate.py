#!/usr/bin/env python3
"""Tests for specback-estimate.py — token estimation & budget gate (Issue #267).

Follows the importlib pattern of test_coverage_check_core.py: the script is
loaded as a module so its functions (``compute_estimate``, ``num_units``,
``calibration_ratio``, ...) can be unit-tested directly, while CLI-level
behaviour (exit codes, --json, --budget-limit) is exercised via subprocess.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(scripts_dir))

_spec = importlib.util.spec_from_file_location(
    "specback_estimate", scripts_dir / "specback-estimate.py"
)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["specback_estimate"] = mod  # register before exec_module
_spec.loader.exec_module(mod)

SCRIPT = scripts_dir / "specback-estimate.py"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_specback(
    tmp_path: Path,
    num_units: int = 3,
    num_chapters: int = 5,
    depth_mode: str = "comprehensive",
    tone: str = "thorough",
    history_runs: list[dict] | None = None,
    inventory_form: str = "list",
) -> Path:
    """Create a ``.specback/`` fixture dir and return it."""
    d = tmp_path / ".specback"
    units = [
        {"file": f"unit-{i}.md", "type": "requirement", "role": "source"}
        for i in range(num_units)
    ]
    if inventory_form == "dict":
        _write_json(d / "inventory.json", {"units": units})
    else:
        _write_json(d / "inventory.json", units)
    _write_json(d / "goal.json", {"depth_mode": depth_mode, "tone": tone})
    _write_json(
        d / "wbs.json",
        {"chapters": [{"id": f"ch-{i}", "title": f"Chapter {i}"}
                      for i in range(num_chapters)]},
    )
    if history_runs is not None:
        _write_json(d / "estimate-history.json", {"runs": history_runs})
    return d


def run_cli(specback_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the CLI as a subprocess against ``specback_dir``."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback_dir),
         *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_main_returns_int_on_missing_input(tmp_path: Path) -> None:
    """main(argv) returns an int exit code instead of None (Issue #286)."""
    rc = mod.main(["--specback-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# 1. Basic estimation
# ---------------------------------------------------------------------------


def test_basic_estimate(tmp_path: Path) -> None:
    """5 chapters / 1000 units / comprehensive / thorough → 310000."""
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)
    proc = run_cli(d)
    assert proc.returncode == 0, proc.stderr
    assert "Estimated tokens: 310,000" in proc.stdout
    assert "Chapters: 5" in proc.stdout
    assert "Units: 1000" in proc.stdout
    assert "depth_mode: comprehensive" in proc.stdout
    assert "tone: thorough" in proc.stdout


def test_raw_formula_matches_spec() -> None:
    """raw = 2000*chapters + 300*units."""
    raw = (mod.BASE_TOKENS_PER_CHAPTER * 5
           + mod.TOKENS_PER_UNIT * 1000)
    assert raw == 310000


# ---------------------------------------------------------------------------
# 2. depth_mode / tone factors
# ---------------------------------------------------------------------------


def test_depth_mode_factor_halves_outline() -> None:
    """outline is exactly half of comprehensive."""
    comp = mod.compute_estimate(5, 1000, "comprehensive", "thorough")
    outline = mod.compute_estimate(5, 1000, "outline", "thorough")
    assert outline["estimated_tokens"] == comp["estimated_tokens"] // 2


def test_depth_mode_interactive_factor() -> None:
    """interactive = 0.8 × comprehensive (spec value literal, not self-referential)."""
    comp = mod.compute_estimate(5, 1000, "comprehensive", "thorough")
    inter = mod.compute_estimate(5, 1000, "interactive", "thorough")
    assert inter["estimated_tokens"] == int(comp["estimated_tokens"] * 0.8)


def test_tone_concise_smaller_than_thorough() -> None:
    """concise = 0.7 × thorough (spec value literal, not self-referential)."""
    thorough = mod.compute_estimate(5, 1000, "comprehensive", "thorough")
    concise = mod.compute_estimate(5, 1000, "comprehensive", "concise")
    assert concise["estimated_tokens"] == int(
        thorough["estimated_tokens"] * 0.7
    )


def test_factor_values_match_spec() -> None:
    """Pin the spec factor values independently of the implementation."""
    assert mod.DEPTH_MODE_FACTOR == {
        "comprehensive": 1.0,
        "interactive": 0.8,
        "outline": 0.5,
    }
    assert mod.TONE_FACTOR == {"thorough": 1.0, "concise": 0.7}


def test_unknown_depth_mode_uses_factor_one(tmp_path: Path, capsys) -> None:
    """Unknown depth_mode → factor 1.0 + stderr warning, exit 0."""
    d = make_specback(tmp_path, num_units=1000, num_chapters=5,
                      depth_mode="bogus-mode")
    proc = run_cli(d)
    assert proc.returncode == 0, proc.stderr
    assert "Estimated tokens: 310,000" in proc.stdout
    assert "unknown depth_mode 'bogus-mode'" in proc.stderr


# ---------------------------------------------------------------------------
# 3. --json output
# ---------------------------------------------------------------------------


def test_json_output_structure_and_values(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data == {
        "estimated_tokens": 310000,
        "num_chapters": 5,
        "num_units": 1000,
        "depth_mode": "comprehensive",
        "tone": "thorough",
        "calibration_ratio": None,
        "calibration_runs": 0,
    }


# ---------------------------------------------------------------------------
# 4. Budget gate
# ---------------------------------------------------------------------------


def test_budget_limit_exceeded_exits_2(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)  # 310000
    proc = run_cli(d, "--budget-limit", "300000")
    assert proc.returncode == 2
    assert "exceed budget limit" in proc.stderr
    assert "outline" in proc.stderr  # guidance to switch depth_mode


def test_budget_limit_within_exits_0(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)  # 310000
    proc = run_cli(d, "--budget-limit", "400000")
    assert proc.returncode == 0, proc.stderr
    assert "Estimated tokens: 310,000" in proc.stdout


# ---------------------------------------------------------------------------
# 5. Calibration from estimate-history.json
# ---------------------------------------------------------------------------

_RUNS = [
    {"timestamp": "2026-01-01T00:00:00Z", "depth_mode": "comprehensive",
     "tone": "thorough", "num_chapters": 2, "num_units": 50,
     "estimated_tokens": 1000, "actual_tokens": 2000},   # ratio 2.0
    {"timestamp": "2026-01-02T00:00:00Z", "depth_mode": "comprehensive",
     "tone": "thorough", "num_chapters": 2, "num_units": 50,
     "estimated_tokens": 1000, "actual_tokens": 1000},   # ratio 1.0
    {"timestamp": "2026-01-03T00:00:00Z", "depth_mode": "comprehensive",
     "tone": "thorough", "num_chapters": 2, "num_units": 50,
     "estimated_tokens": 1000, "actual_tokens": 1500},   # ratio 1.5
]  # median = 1.5


def test_calibration_ratio_median() -> None:
    assert mod.calibration_ratio(_RUNS) == 1.5


def test_calibration_requires_three_runs() -> None:
    assert mod.calibration_ratio(_RUNS[:2]) is None


def test_calibration_applied_to_output(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5,
                      history_runs=_RUNS)
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["calibration_ratio"] == 1.5
    assert data["calibration_runs"] == 3
    assert data["estimated_tokens"] == int(310000 * 1.5)


def test_calibration_reported_in_text_output(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5,
                      history_runs=_RUNS)
    proc = run_cli(d)
    assert proc.returncode == 0, proc.stderr
    assert "Calibration: ratio=1.5000 applied from 3 run(s)" in proc.stdout


# ---------------------------------------------------------------------------
# 6. Missing input → exit 1
# ---------------------------------------------------------------------------


def test_missing_inventory_exits_1(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    (d / "inventory.json").unlink()
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "inventory.json" in proc.stderr


def test_missing_goal_exits_1(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    (d / "goal.json").unlink()
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "goal.json" in proc.stderr


def test_missing_specback_dir_exits_1(tmp_path: Path) -> None:
    d = tmp_path / "does-not-exist"
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "inventory.json" in proc.stderr


# ---------------------------------------------------------------------------
# 7. inventory.json dict form {"units": [...]}
# ---------------------------------------------------------------------------


def test_dict_inventory_form(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5,
                      inventory_form="dict")
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["num_units"] == 1000
    assert data["estimated_tokens"] == 310000


def test_dict_and_list_inventory_equivalent(tmp_path: Path) -> None:
    d1 = make_specback(tmp_path / "a", num_units=7, num_chapters=3,
                       inventory_form="list")
    d2 = make_specback(tmp_path / "b", num_units=7, num_chapters=3,
                       inventory_form="dict")
    p1 = run_cli(d1, "--json")
    p2 = run_cli(d2, "--json")
    assert json.loads(p1.stdout)["estimated_tokens"] == \
        json.loads(p2.stdout)["estimated_tokens"]


# ---------------------------------------------------------------------------
# Bonus: --record-actual
# ---------------------------------------------------------------------------


def test_record_actual_appends_run(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)
    proc = run_cli(d, "--record-actual", "300000")
    assert proc.returncode == 0, proc.stderr
    history = json.loads((d / "estimate-history.json").read_text(encoding="utf-8"))
    assert len(history["runs"]) == 1
    entry = history["runs"][0]
    assert entry["actual_tokens"] == 300000
    assert entry["estimated_tokens"] == 310000
    assert entry["num_chapters"] == 5
    assert entry["num_units"] == 1000
    assert entry["depth_mode"] == "comprehensive"
    assert entry["tone"] == "thorough"
    assert "timestamp" in entry


def test_record_actual_preserves_prior_runs(tmp_path: Path) -> None:
    d = make_specback(tmp_path, num_units=1000, num_chapters=5,
                      history_runs=_RUNS)
    proc = run_cli(d, "--record-actual", "400000")
    assert proc.returncode == 0, proc.stderr
    history = json.loads((d / "estimate-history.json").read_text(encoding="utf-8"))
    assert len(history["runs"]) == 4
    assert history["runs"][3]["actual_tokens"] == 400000


# ---------------------------------------------------------------------------
# 8. Broken / malformed input files → graceful errors
# ---------------------------------------------------------------------------


def test_broken_json_inventory_exits_1(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    (d / "inventory.json").write_text("{ not json", encoding="utf-8")
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "failed to read" in proc.stderr


def test_invalid_inventory_shape_exits_1(tmp_path: Path) -> None:
    """inventory.json that is neither a list nor {\"units\": [...]} → exit 1."""
    d = make_specback(tmp_path)
    (d / "inventory.json").write_text("{}", encoding="utf-8")
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "must be a list or an object" in proc.stderr


def test_invalid_wbs_shape_exits_1(tmp_path: Path) -> None:
    """wbs.json whose chapters is not a list → exit 1."""
    d = make_specback(tmp_path)
    (d / "wbs.json").write_text('{"chapters": "oops"}', encoding="utf-8")
    proc = run_cli(d)
    assert proc.returncode == 1
    assert "chapters" in proc.stderr


def test_goal_array_depth_mode_warns_not_crash(tmp_path: Path) -> None:
    """depth_mode as a list must not crash (unhashable) — warns, factor 1.0."""
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)
    (d / "goal.json").write_text(
        '{"depth_mode": ["x"], "tone": "thorough"}', encoding="utf-8"
    )
    proc = run_cli(d)
    assert proc.returncode == 0, proc.stderr
    assert "unknown depth_mode" in proc.stderr
    assert "Estimated tokens: 310,000" in proc.stdout


# ---------------------------------------------------------------------------
# 9. Input validation — --record-actual / --budget-limit positive-only
# ---------------------------------------------------------------------------


def test_negative_record_actual_rejected(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    proc = run_cli(d, "--record-actual", "-500")
    assert proc.returncode == 2  # argparse ArgumentTypeError
    assert "positive integer" in proc.stderr


def test_zero_record_actual_rejected(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    proc = run_cli(d, "--record-actual", "0")
    assert proc.returncode == 2
    assert "positive integer" in proc.stderr


def test_negative_budget_limit_rejected(tmp_path: Path) -> None:
    d = make_specback(tmp_path)
    proc = run_cli(d, "--budget-limit", "-1")
    assert proc.returncode == 2
    assert "positive integer" in proc.stderr


def test_budget_limit_equal_boundary(tmp_path: Path) -> None:
    """estimated == budget-limit → exit 0 (`>` not `>=`)."""
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)  # 310000
    proc = run_cli(d, "--budget-limit", "310000")
    assert proc.returncode == 0, proc.stderr
    proc = run_cli(d, "--budget-limit", "309999")
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# 10. History hardening — symlink, NaN/Infinity, corrupt file
# ---------------------------------------------------------------------------


def test_record_actual_refuses_symlink(tmp_path: Path) -> None:
    """estimate-history.json as symlink → refused, target untouched."""
    d = make_specback(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    (d / "estimate-history.json").symlink_to(victim)
    proc = run_cli(d, "--record-actual", "300000")
    assert proc.returncode == 1
    assert "symlink" in proc.stderr
    assert victim.read_text(encoding="utf-8") == "{}"


def test_non_finite_history_rejected_and_quarantined(tmp_path: Path) -> None:
    """NaN/Infinity in history → rejected, quarantined to .bak, fresh start."""
    d = make_specback(tmp_path, num_units=1000, num_chapters=5)
    (d / "estimate-history.json").write_text(
        '{"runs": [{"estimated_tokens": 1000, "actual_tokens": NaN},'
        ' {"estimated_tokens": 1000, "actual_tokens": 2000}]}',
        encoding="utf-8",
    )
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    assert "corrupt estimate-history.json" in proc.stderr
    assert (d / "estimate-history.json.bak").exists()
    data = json.loads(proc.stdout)
    assert data["calibration_runs"] == 0
    assert data["estimated_tokens"] == 310000


def test_corrupt_history_quarantined_and_continues(tmp_path: Path) -> None:
    d = make_specback(tmp_path, history_runs=_RUNS)
    (d / "estimate-history.json").write_text("{broken", encoding="utf-8")
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    assert "corrupt estimate-history.json" in proc.stderr
    assert (d / "estimate-history.json.bak").exists()
    data = json.loads(proc.stdout)
    assert data["calibration_ratio"] is None  # quarantine reset history


def test_history_runs_capped_at_max(tmp_path: Path) -> None:
    """load_runs caps history to the last MAX_HISTORY_RUNS entries."""
    d = tmp_path / ".specback"
    _write_json(d / "estimate-history.json", {"runs": [
        {"estimated_tokens": 1000, "actual_tokens": 1500}
        for _ in range(60)
    ]})
    runs = mod.load_runs(d / "estimate-history.json")
    assert len(runs) == mod.MAX_HISTORY_RUNS
    assert mod.calibration_ratio(runs) == 1.5


# ---------------------------------------------------------------------------
# 11. Calibration input filtering
# ---------------------------------------------------------------------------


def test_calibration_skips_invalid_entries() -> None:
    """Non-numeric / non-positive / non-finite entries are excluded."""
    runs = [
        {"estimated_tokens": 1000, "actual_tokens": "oops"},   # non-numeric
        {"estimated_tokens": 0, "actual_tokens": 1500},        # est <= 0
        {"estimated_tokens": 1000, "actual_tokens": 0},        # act <= 0
        {"estimated_tokens": 1000, "actual_tokens": 2000},     # ratio 2.0
        {"estimated_tokens": 1000, "actual_tokens": 1000},     # ratio 1.0
        {"estimated_tokens": 1000, "actual_tokens": 3000},     # ratio 3.0
    ]
    assert mod.calibration_ratio(runs) == 2.0  # median of 2.0, 1.0, 3.0
    assert len(mod.usable_ratios(runs)) == 3


def test_zero_units_and_chapters_ok(tmp_path: Path) -> None:
    """Empty inventory / zero chapters → estimate 0, exit 0."""
    d = make_specback(tmp_path, num_units=0, num_chapters=0)
    proc = run_cli(d, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["num_units"] == 0
    assert data["num_chapters"] == 0
    assert data["estimated_tokens"] == 0


# ---------------------------------------------------------------------------
# 12. Direct unit tests for helper functions (pre-commit coverage gate)
# ---------------------------------------------------------------------------


def test_positive_int_accepts_and_rejects() -> None:
    """positive_int accepts positive ints, rejects non-numeric/0/negative."""
    assert mod.positive_int("42") == 42
    for bad in ("abc", "0", "-1", "1.5"):
        with pytest.raises(argparse.ArgumentTypeError):
            mod.positive_int(bad)


def test_sanitize_text_strips_control_chars() -> None:
    """sanitize_text removes control chars but keeps printable text."""
    assert mod.sanitize_text("\x1b[2J fake \x00 title") == "[2J fake  title"
    assert mod.sanitize_text("comprehensive") == "comprehensive"
    assert mod.sanitize_text(["a", "b"]) == "['a', 'b']"


def test_load_json_rejects_nonfinite(tmp_path: Path) -> None:
    """load_json rejects NaN / Infinity via parse_constant."""
    p = tmp_path / "bad.json"
    p.write_text('{"x": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        mod.load_json(p)


def test_load_json_rejects_oversized(tmp_path: Path) -> None:
    """load_json rejects files larger than MAX_INPUT_BYTES."""
    p = tmp_path / "big.json"
    p.write_text(" " * (mod.MAX_INPUT_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        mod.load_json(p)


def test_factor_for_non_string_returns_one() -> None:
    """factor_for handles non-string values without unhashable TypeError."""
    assert mod.factor_for(["x"], mod.DEPTH_MODE_FACTOR, "depth_mode") == 1.0
    assert mod.factor_for(None, mod.TONE_FACTOR, "tone") == 1.0
    assert mod.factor_for("outline", mod.DEPTH_MODE_FACTOR, "depth_mode") == 0.5


def test_record_actual_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    """record_actual writes atomically and leaves no .tmp file behind."""
    d = tmp_path / ".specback"
    d.mkdir(parents=True)
    entry = {"timestamp": "2026-01-01T00:00:00Z", "actual_tokens": 1000}
    mod.record_actual(d / "estimate-history.json", entry)
    assert (d / "estimate-history.json").exists()
    assert not (d / "estimate-history.json.tmp").exists()
    data = json.loads((d / "estimate-history.json").read_text(encoding="utf-8"))
    assert data["runs"] == [entry]


def test_record_actual_refuses_symlink_direct(tmp_path: Path) -> None:
    """record_actual raises ValueError on a symlink target (direct call)."""
    d = tmp_path / ".specback"
    d.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    (d / "estimate-history.json").symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        mod.record_actual(d / "estimate-history.json", {"actual_tokens": 1})
    assert victim.read_text(encoding="utf-8") == "{}"


def test_record_actual_ignores_planted_tmp_symlink(tmp_path: Path) -> None:
    """Fixed-name .tmp symlink (H-2) must never be followed for the write."""
    d = tmp_path / ".specback"
    d.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("SAFE", encoding="utf-8")
    # Attacker plants a symlink at the old fixed-name temp location.
    (d / "estimate-history.json.tmp").symlink_to(victim)
    mod.record_actual(d / "estimate-history.json", {"actual_tokens": 1})
    assert (d / "estimate-history.json").exists()
    data = json.loads((d / "estimate-history.json").read_text(encoding="utf-8"))
    assert data["runs"][0]["actual_tokens"] == 1
    # The victim was never overwritten through the planted symlink.
    assert victim.read_text(encoding="utf-8") == "SAFE"


def test_reject_nonfinite_via_common(tmp_path: Path) -> None:
    """load_json rejects NaN via the shared common.reject_nonfinite hook."""
    p = tmp_path / "bad.json"
    p.write_text('{"v": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        mod.load_json(p)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
