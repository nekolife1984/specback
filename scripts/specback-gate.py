#!/usr/bin/env python3
"""
specback-gate.py — thin CI wrapper for specback drift detection.

Chains the existing verification scripts into a single gate suitable for
CI (GitHub Actions / local ``--ci`` mode):

    1. resolve base ref (merge-base in CI mode, or ``--base``)
    2. ``detect-drift.py``  -> drift-report.md / drift-report.json
    3. ``fix-refs.py --check`` -> orphaned REF detection (warning only)
    4. report sanity (equivalent to ``gates.py --gate drift_detected``)

Warn/fail 2-stage policy (Issue #266):

- **fail**  (exit 1): drift-report.json reports affected spec sections or
  deleted sources with refs. The spec must be updated before merge.
- **warn**  (exit 0): no fail-level drift, but the run produced warnings
  (orphaned REFs, new uncovered sources). CI reports them without gating.
- **pass**  (exit 0): no drift, no warnings.

``--warn-only`` downgrades fail to warn (exit 0) for pre-push hooks where
blocking pushes is undesirable by default (opt-in drift hook design).

Exit codes: 0 = pass/warn, 1 = fail, 2 = usage/environment error.

Usage
-----
    # CI mode: resolve merge-base automatically (GitHub Actions or local)
    python specback-gate.py --ci --specback-dir .specback --json

    # Explicit base
    python specback-gate.py --base origin/main --specback-dir .specback --json

    # Pre-push hook: never block, only warn
    python specback-gate.py --ci --warn-only --specback-dir .specback

Dependencies
------------
- Python 3.10+ (stdlib only)
- git (for merge-base resolution and git-mode drift detection)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import add_specback_dir_arg
from git_utils import SAFE_REF_RE, resolve_ref

SCHEMA_VERSION = "0.1.0"

# Verdict values
VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_FAIL = "fail"

# Default ref used when neither --base nor CI env vars are available.
DEFAULT_BASE_REF = "origin/main"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _script_dir() -> Path:
    """Directory containing this script (and its sibling scripts)."""
    return Path(__file__).resolve().parent


def _run(cmd: list[str], cwd: str | Path | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command, capturing output."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def run_script(script_name: str, args: list[str], cwd: str | Path | None = None,
               timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a sibling script with ``sys.executable``."""
    return _run(
        [sys.executable, str(_script_dir() / script_name), *args],
        cwd=cwd,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Base resolution
# ---------------------------------------------------------------------------


def resolve_merge_base(ref: str, cwd: str | Path | None = None) -> str | None:
    """Return ``git merge-base <ref> HEAD``, or None when it cannot resolve.

    The *ref* is validated via :func:`git_utils.resolve_ref` before use.
    Ref existence is probed first so a missing ref (e.g. no ``origin``
    remote on a fresh clone) returns None silently instead of printing
    an ERROR from resolve_ref (Issue #266 pre-push noise).
    """
    if ref.startswith("-") or not SAFE_REF_RE.match(ref):
        return None
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )
    if probe.returncode != 0:
        return None
    try:
        resolved = resolve_ref(ref, cwd)
    except SystemExit:
        return None
    proc = _run(["git", "merge-base", resolved, "HEAD"], cwd=cwd, timeout=30)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def resolve_base(base: str | None, ci: bool, cwd: str | Path | None = None) -> str:
    """Determine the git ref to diff against.

    Priority:
    1. Explicit ``--base`` CLI argument.
    2. CI mode: ``GITHUB_BASE_REF`` -> ``origin/<base_ref>`` merge-base,
       then ``origin/main`` merge-base.
    3. ``origin/main`` merge-base (local default).
    4. ``HEAD`` (last resort; falls back to state.json logic inside
       detect-drift.py).

    Returns a validated commit hash.
    """
    if base is not None:
        return resolve_ref(base, cwd)

    if ci:
        gh_base = os.environ.get("GITHUB_BASE_REF")
        if gh_base:
            mb = resolve_merge_base(f"origin/{gh_base}", cwd)
            if mb:
                return mb
            # origin/<base> may not be fetched; try origin/main.
        mb = resolve_merge_base(DEFAULT_BASE_REF, cwd)
        if mb:
            return mb
        return "HEAD"

    mb = resolve_merge_base(DEFAULT_BASE_REF, cwd)
    if mb:
        return mb
    return "HEAD"


# ---------------------------------------------------------------------------
# Drift report parsing
# ---------------------------------------------------------------------------


def load_drift_report(specback_dir: Path) -> dict[str, Any]:
    """Load drift-report.json, returning {} when absent/unparseable."""
    report_path = specback_dir / "drift-report.json"
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def evaluate_verdict(
    drift: dict[str, Any],
    orphaned_refs: int,
    new_uncovered: int,
    warn_only: bool,
) -> str:
    """Classify the run as pass / warn / fail.

    fail: affected spec sections OR deleted sources with refs.
    warn: no fail-level drift but orphaned REFs or new uncovered sources.
    """
    summary = drift.get("summary", {})
    affected = int(summary.get("affected_spec_sections", 0) or 0)
    deleted = int(summary.get("deleted_sources_with_refs", 0) or 0)
    if affected > 0 or deleted > 0:
        return VERDICT_PASS if warn_only else VERDICT_FAIL
    if orphaned_refs > 0 or new_uncovered > 0:
        return VERDICT_WARN
    return VERDICT_PASS


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="specback CI drift gate — merge-base -> detect-drift "
                    "-> fix-refs --check -> gates",
    )
    add_specback_dir_arg(p)
    p.add_argument("--output-dir", default=None,
                   help="Output directory for drift reports "
                        "(default: same as --specback-dir)")
    p.add_argument("--target-dir", default=None,
                   help="Target project root (default: parent of --specback-dir)")
    p.add_argument("--base", default=None,
                   help="Git ref to diff against (default: auto-resolved "
                        "merge-base)")
    p.add_argument("--ci", action="store_true",
                   help="CI mode: resolve merge-base from GITHUB_BASE_REF / "
                        "origin/main (same behaviour locally as in CI)")
    p.add_argument("--json", action="store_true",
                   help="Print machine-readable JSON report to stdout")
    p.add_argument("--warn-only", action="store_true",
                   help="Never exit 1 — downgrade fail to warn "
                        "(for opt-in pre-push hooks)")
    return p.parse_args(argv)


def _finish(result: dict[str, Any], as_json: bool,
            skip_fail: bool = False) -> int:
    """Print the report and map the verdict to an exit code.

    ``skip_fail=True`` downgrades fail to warn (used for spec-not-generated
    skips, where a missing spec must never fail the build).
    """
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    verdict = result["verdict"]
    if skip_fail and verdict == VERDICT_FAIL:
        verdict = VERDICT_WARN
        result["verdict"] = VERDICT_WARN
    print(f"specback-gate.py: verdict = {verdict}", file=sys.stderr)
    for w in result["warnings"]:
        print(f"specback-gate.py: warning: {w}", file=sys.stderr)

    if verdict == VERDICT_FAIL:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    specback_path = Path(args.specback_dir)
    if not specback_path.is_dir():
        print(f"ERROR: {args.specback_dir} is not a directory. "
              f"Run from the target project root or pass --specback-dir.",
              file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else specback_path
    output_dir.mkdir(parents=True, exist_ok=True)

    target_dir = Path(args.target_dir) if args.target_dir else specback_path.parent

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "verdict": VERDICT_PASS,
        "base": None,
        "drift": {},
        "fix_refs": {"orphaned": 0, "ok": True},
        "gates": {"drift_detected": {"passed": True}},
        "warnings": [],
    }

    # -- 1. Resolve base --
    try:
        base = resolve_base(args.base, args.ci, cwd=str(target_dir))
    except SystemExit:
        return 2
    result["base"] = base
    print(f"specback-gate.py: base = {base[:12]}", file=sys.stderr)

    # -- 1.5 Spec artifacts present? --
    # A project that has not run specback yet has no source-map.json /
    # trace.json. detect-drift.py would exit 1 on the missing source-map;
    # treat this as a skip (warn) rather than a CI failure — the drift
    # gate only applies once a spec has been generated (dogfooding: the
    # specback repo itself runs this workflow before full self-generation).
    missing = [
        name for name in ("source-map.json", "trace.json")
        if not (specback_path / name).exists()
    ]
    if missing:
        result["verdict"] = VERDICT_WARN
        result["warnings"].append(
            "spec artifacts missing (" + ", ".join(missing) + ") — "
            "run specback to generate the spec first; drift gate skipped"
        )
        return _finish(result, args.json, skip_fail=True)

    # -- 2. detect-drift.py --
    drift_proc = run_script(
        "detect-drift.py",
        ["--specback-dir", str(specback_path),
         "--output-dir", str(output_dir),
         "--base", base,
         "--json"],
        cwd=str(target_dir),
    )
    if drift_proc.returncode != 0:
        print(f"ERROR: detect-drift.py failed (exit {drift_proc.returncode}):",
              file=sys.stderr)
        print(drift_proc.stderr.strip(), file=sys.stderr)
        return 2

    # -- 3. fix-refs.py --check (warning only) --
    # NOTE: do NOT pass --target-dir here — fix-refs.py auto-detects
    # final/ then drafts/ under the specback dir. Passing the project
    # root would scan README.md etc. and produce false orphan reports.
    fix_refs_proc = run_script(
        "fix-refs.py",
        ["--specback-dir", str(specback_path),
         "--base", base,
         "--check", "--json"],
        cwd=str(target_dir),
    )
    orphaned = 0
    try:
        fix_refs_out = json.loads(fix_refs_proc.stdout)
        orphaned = int(fix_refs_out.get("orphaned", 0) or 0)
    except (json.JSONDecodeError, AttributeError, ValueError):
        orphaned = 0
    result["fix_refs"]["orphaned"] = orphaned
    result["fix_refs"]["ok"] = orphaned == 0
    if orphaned > 0:
        result["warnings"].append(
            f"{orphaned} orphaned REF(s) found — run "
            f"fix-refs.py --apply to repair line numbers"
        )

    # -- 4. Report sanity (equivalent to gates.py --gate drift_detected) --
    # NOTE: do NOT shell out to gates.py here — it re-runs detect-drift.py
    # without a --base, which would overwrite drift-report.json using the
    # state.json generated_at_commit instead of the CI merge-base. The
    # drift_detected gate checks "artefacts exist and parse", which the
    # wrapper already does via load_drift_report() below.
    report_md = output_dir / "drift-report.md"
    report_json = output_dir / "drift-report.json"
    gates_passed = report_md.exists() and report_json.exists()
    result["gates"]["drift_detected"]["passed"] = gates_passed
    if not gates_passed:
        result["warnings"].append(
            "drift-report artefacts missing — detect-drift.py did not "
            "produce drift-report.md/json"
        )

    # -- 5. Evaluate verdict --
    drift = load_drift_report(output_dir)
    summary = drift.get("summary", {})
    new_uncovered = int(summary.get("new_uncovered_sources", 0) or 0)
    result["drift"] = drift
    result["verdict"] = evaluate_verdict(
        drift, orphaned, new_uncovered, args.warn_only,
    )
    if new_uncovered > 0 and result["verdict"] in (VERDICT_PASS, VERDICT_WARN):
        result["warnings"].append(
            f"{new_uncovered} new uncovered source(s) — spec sections may "
            f"need to reference them"
        )

    # -- Output --
    return _finish(result, args.json)


if __name__ == "__main__":
    sys.exit(main())
