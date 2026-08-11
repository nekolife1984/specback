#!/usr/bin/env python3
"""
specback-estimate.py — Estimate Phase 3 subagent token consumption (Issue #267).

Reads planning artefacts from ``--specback-dir`` and prints an estimate of
the tokens a Phase 3 subagent will consume while writing the spec:

    raw = BASE_TOKENS_PER_CHAPTER * num_chapters + TOKENS_PER_UNIT * num_units
    raw *= DEPTH_MODE_FACTOR[depth_mode] * TONE_FACTOR[tone]

Inputs (all under ``--specback-dir``, default ``.specback``):

    inventory.json          unit list (``[...]`` or ``{"units": [...]}``)
    goal.json               ``depth_mode`` + ``tone``
    wbs.json                ``chapters[]``
    estimate-history.json   optional; prior runs used for calibration

When at least three runs with both ``estimated_tokens`` and ``actual_tokens``
are recorded in ``estimate-history.json``, the raw estimate is corrected by
the median ``actual / estimated`` ratio.

Usage
-----
    python specback-estimate.py --specback-dir .specback
    python specback-estimate.py --specback-dir .specback --json
    python specback-estimate.py --specback-dir .specback --budget-limit 400000
    python specback-estimate.py --specback-dir .specback --record-actual 318502

Exit codes
----------
    0 — estimate computed (and within budget when ``--budget-limit`` given)
    1 — required input missing or unreadable (inventory.json / goal.json / wbs.json)
    2 — estimated tokens exceed ``--budget-limit``
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Estimation constants (Issue #267)
# ---------------------------------------------------------------------------

BASE_TOKENS_PER_CHAPTER = 2000
TOKENS_PER_UNIT = 300
DEPTH_MODE_FACTOR: dict[str, float] = {
    "comprehensive": 1.0,
    "interactive": 0.8,
    "outline": 0.5,
}
TONE_FACTOR: dict[str, float] = {
    "thorough": 1.0,
    "concise": 0.7,
}

REQUIRED_FILES = ("inventory.json", "goal.json", "wbs.json")
HISTORY_FILE = "estimate-history.json"
MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB guard against unbounded reads
MAX_HISTORY_RUNS = 50  # cap recorded runs so old anomalies fade out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def positive_int(raw: str) -> int:
    """argparse type: require an integer > 0 (rejects 0 and negatives)."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def _reject_nonfinite(name: str) -> Any:
    """json.loads parse_constant hook — reject NaN/Infinity as invalid JSON."""
    raise ValueError(f"non-finite JSON constant: {name}")


def load_json(path: Path) -> Any:
    """Read and parse ``path``; raises OSError / ValueError on failure.

    Guards against unbounded reads (size cap) and rejects NaN / Infinity
    constants so a corrupt history file cannot crash calibration later.
    """
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValueError(
            f"{path}: file too large ({size} bytes > {MAX_INPUT_BYTES} bytes)"
        )
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_nonfinite,
    )


def sanitize_text(value: Any) -> str:
    """Strip control characters from a value for terminal / CI output."""
    return "".join(
        ch for ch in str(value) if ch.isprintable() or ch in "\t\n"
    )


def num_units(inventory: Any) -> int:
    """Unit count from inventory.json — list or ``{"units": [...]}`` form."""
    if isinstance(inventory, list):
        return len(inventory)
    if isinstance(inventory, dict) and isinstance(inventory.get("units"), list):
        return len(inventory["units"])
    raise ValueError(
        'inventory.json must be a list or an object with a "units" list'
    )


def num_chapters(wbs: Any) -> int:
    """Chapter count from wbs.json ``chapters`` list."""
    if isinstance(wbs, dict) and isinstance(wbs.get("chapters"), list):
        return len(wbs["chapters"])
    raise ValueError('wbs.json must be an object with a "chapters" list')


def factor_for(value: Any, table: dict[str, float], kind: str) -> float:
    """Look up ``value`` in ``table``; warn on stderr and return 1.0 when
    the value is unknown or not a string (also guards unhashable types)."""
    if isinstance(value, str) and value in table:
        return table[value]
    safe = sanitize_text(value)
    print(
        f"warning: unknown {kind} {safe!r} — using factor 1.0",
        file=sys.stderr,
    )
    return 1.0


def compute_estimate(
    num_chapters: int,
    num_units: int,
    depth_mode: str,
    tone: str,
) -> dict[str, Any]:
    """Compute the token estimate for one run.

    Returns a dict with ``raw``, ``depth_factor``, ``tone_factor`` and
    ``estimated_tokens`` (``raw`` adjusted by both factors).
    """
    depth_factor = factor_for(depth_mode, DEPTH_MODE_FACTOR, "depth_mode")
    tone_factor = factor_for(tone, TONE_FACTOR, "tone")
    raw = BASE_TOKENS_PER_CHAPTER * num_chapters + TOKENS_PER_UNIT * num_units
    return {
        "raw": raw,
        "depth_factor": depth_factor,
        "tone_factor": tone_factor,
        "estimated_tokens": int(raw * depth_factor * tone_factor),
    }


def load_runs(history_path: Path) -> list[dict[str, Any]]:
    """Load recorded runs from estimate-history.json (empty list if absent).

    A corrupt / oversized / non-JSON history is quarantined to a ``.bak``
    sibling and treated as empty, so the tool keeps working instead of
    crashing with an uncaught traceback.
    """
    if not history_path.exists():
        return []
    try:
        data = load_json(history_path)
    except (OSError, ValueError) as exc:
        backup = history_path.with_name(history_path.name + ".bak")
        try:
            os.replace(history_path, backup)
        except OSError:
            pass
        print(
            f"warning: corrupt estimate-history.json ({exc}) — moved to "
            f"{backup}, starting fresh",
            file=sys.stderr,
        )
        return []
    runs = data.get("runs", []) if isinstance(data, dict) else []
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)][-MAX_HISTORY_RUNS:]


def usable_ratios(runs: list[dict[str, Any]]) -> list[float]:
    """Ratios ``actual_tokens / estimated_tokens`` for usable entries.

    Skips entries with non-numeric, non-positive, or non-finite values so
    a bad record cannot corrupt calibration.
    """
    ratios: list[float] = []
    for r in runs:
        est = r.get("estimated_tokens")
        act = r.get("actual_tokens")
        if (isinstance(est, (int, float)) and isinstance(act, (int, float))
                and est > 0 and act > 0
                and math.isfinite(est) and math.isfinite(act)):
            ratios.append(act / est)
    return ratios


def calibration_ratio(runs: list[dict[str, Any]]) -> float | None:
    """Median ``actual_tokens / estimated_tokens`` across usable runs.

    Returns ``None`` when fewer than three usable runs are recorded.
    """
    ratios = usable_ratios(runs)
    if len(ratios) < 3:
        return None
    return statistics.median(ratios)


def record_actual(history_path: Path, entry: dict[str, Any]) -> None:
    """Append ``entry`` to estimate-history.json (indent=2, ensure_ascii=False).

    Writes atomically (temp file + ``os.replace``) and refuses to write
    through a symlink, so an attacker-controlled repo cannot overwrite an
    arbitrary file via ``--record-actual``.
    """
    if history_path.is_symlink():
        raise ValueError(f"refusing to write through symlink: {history_path}")
    runs = load_runs(history_path)
    runs.append(entry)
    tmp = history_path.with_name(history_path.name + ".tmp")
    tmp.write_text(
        json.dumps({"runs": runs}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, history_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(
        description="Estimate Phase 3 subagent token consumption (Issue #267).",
    )
    p.add_argument(
        "--specback-dir", default=".specback",
        help="Path to .specback/ directory (default: .specback)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Output a single JSON object on stdout",
    )
    p.add_argument(
        "--budget-limit", type=positive_int, default=None, metavar="TOKENS",
        help="Exit 2 with a warning when the estimate exceeds this budget",
    )
    p.add_argument(
        "--record-actual", type=positive_int, default=None, metavar="TOKENS",
        help="Append this run's estimate and the actual token count to "
             "estimate-history.json for post-hoc calibration",
    )
    args = p.parse_args()

    specback_dir = Path(args.specback_dir)

    # Required inputs — fail loudly and name the missing file.
    for name in REQUIRED_FILES:
        path = specback_dir / name
        if not path.exists():
            print(f"error: required input missing: {path}", file=sys.stderr)
            sys.exit(1)

    try:
        inventory = load_json(specback_dir / "inventory.json")
        goal = load_json(specback_dir / "goal.json")
        wbs = load_json(specback_dir / "wbs.json")
    except (OSError, ValueError) as exc:
        print(f"error: failed to read {specback_dir}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        n_units = num_units(inventory)
        n_chapters = num_chapters(wbs)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    depth_mode = goal.get("depth_mode", "") if isinstance(goal, dict) else ""
    tone = goal.get("tone", "") if isinstance(goal, dict) else ""

    result = compute_estimate(n_chapters, n_units, depth_mode, tone)
    estimated = result["estimated_tokens"]

    # Calibration from prior recorded runs.
    history_path = specback_dir / HISTORY_FILE
    runs = load_runs(history_path)
    ratio = calibration_ratio(runs)
    usable = len(usable_ratios(runs))
    if ratio is not None:
        estimated = int(estimated * ratio)
    result["estimated_tokens"] = estimated
    result["calibration_ratio"] = ratio
    result["calibration_runs"] = usable

    # Post-hoc calibration: record this run's estimate vs. actual tokens.
    if args.record_actual is not None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "depth_mode": depth_mode,
            "tone": tone,
            "num_chapters": n_chapters,
            "num_units": n_units,
            "estimated_tokens": estimated,
            "actual_tokens": args.record_actual,
        }
        try:
            record_actual(history_path, entry)
        except (OSError, ValueError) as exc:
            print(f"error: failed to record actuals: {exc}", file=sys.stderr)
            sys.exit(1)

    # Output.
    if args.json:
        print(json.dumps({
            "estimated_tokens": estimated,
            "num_chapters": n_chapters,
            "num_units": n_units,
            "depth_mode": depth_mode,
            "tone": tone,
            "calibration_ratio": ratio,
            "calibration_runs": usable,
        }))
    else:
        print(f"Estimated tokens: {estimated:,}")
        print(f"Chapters: {n_chapters}")
        print(f"Units: {n_units}")
        print(f"depth_mode: {sanitize_text(depth_mode)}")
        print(f"tone: {sanitize_text(tone)}")
        if ratio is not None:
            print(
                f"Calibration: ratio={ratio:.4f} applied from {usable} run(s)"
            )

    # Budget gate — warn on stderr and exit 2 when over budget.
    if args.budget_limit is not None and estimated > args.budget_limit:
        print(
            f"warning: estimated tokens ({estimated:,}) exceed budget limit "
            f"({args.budget_limit:,}). Switch depth_mode to 'outline' and "
            "re-run to bring the estimate under budget.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
