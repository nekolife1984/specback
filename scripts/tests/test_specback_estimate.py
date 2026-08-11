#!/usr/bin/env python3
"""Tests for specback-estimate.py — token estimation & budget gate (Issue #267).

Follows the importlib pattern of test_coverage_check_core.py: the script is
loaded as a module so its functions (``compute_estimate``, ``num_units``,
``calibration_ratio``, ...) can be unit-tested directly, while CLI-level
behaviour (exit codes, --json, --budget-limit) is exercised via subprocess.
"""

from __future__ import annotations

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
    )


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
    """interactive = 0.8 × comprehensive."""
    comp = mod.compute_estimate(5, 1000, "comprehensive", "thorough")
    inter = mod.compute_estimate(5, 1000, "interactive", "thorough")
    assert inter["estimated_tokens"] == int(
        comp["estimated_tokens"] * mod.DEPTH_MODE_FACTOR["interactive"]
    )


def test_tone_concise_smaller_than_thorough() -> None:
    """concise is smaller than thorough for the same inputs."""
    thorough = mod.compute_estimate(5, 1000, "comprehensive", "thorough")
    concise = mod.compute_estimate(5, 1000, "comprehensive", "concise")
    assert concise["estimated_tokens"] < thorough["estimated_tokens"]
    assert concise["estimated_tokens"] == int(
        thorough["estimated_tokens"] * mod.TONE_FACTOR["concise"]
    )


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
