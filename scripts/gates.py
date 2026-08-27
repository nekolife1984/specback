#!/usr/bin/env python3
"""
gates.py — Unified Gate interface for specback verification checks.

Each gate wraps an existing verification script under a common ``GateReport``
interface.  Gates are callables with the signature::

    gate(**kwargs) -> GateReport

This makes verification results uniform across all specback phases and
provides a natural bridge toward the ADW / typed-envelope pattern (Issues
#203, #204).

Usage
-----
    from gates import coverage_mece, schema_valid

    report = coverage_mece(specback_dir=".specback", output_dir=".")
    if report.passed:
        print("✓ All checks passed")

    # Standalone CLI
    python gates.py --gate coverage_mece --specback-dir .specback

Exit codes
----------
    0 — gate passed (every check returned ok=True)
    1 — gate failed (at least one check returned ok=False)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import reject_nonfinite

# ---------------------------------------------------------------------------
# GateReport
# ---------------------------------------------------------------------------


class GateReport:
    """Standard verification report for one specback gate.

    Each gate produces exactly one ``GateReport`` containing individual
    ``check(item, ok, note)`` entries.  Consumers iterate ``checks`` to
    decide pass / fail at whatever granularity they need.
    """

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._checks: list[dict[str, Any]] = []

    # ---- builder interface ------------------------------------------------

    def check(self, item: str, ok: bool, note: str = "") -> None:
        """Record one verification result."""
        self._checks.append({"item": item, "ok": ok, "note": note})

    # ---- query interface --------------------------------------------------

    @property
    def passed(self) -> bool:
        """``True`` when every recorded check returned ``ok=True``."""
        return all(c["ok"] for c in self._checks)

    @property
    def failures(self) -> list[dict[str, Any]]:
        """Checks that did *not* pass."""
        return [c for c in self._checks if not c["ok"]]

    @property
    def summary(self) -> str:
        """Human-readable one-liner, e.g. ``✓ PASS coverage_mece: 12/12``."""
        total = len(self._checks)
        ok_n = sum(1 for c in self._checks if c["ok"])
        status = "✓ PASS" if self.passed else "✗ FAIL"
        return f"{status} {self.name}: {ok_n}/{total} checks passed"

    # ---- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "gate": self.name,
            "passed": self.passed,
            "check_count": len(self._checks),
            "checks": self._checks.copy(),
            "summary": self.summary,
        }

    def __str__(self) -> str:
        return self.summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_skill_path(specback_dir: str) -> Path:
    """Return the skill root from ``{specback_dir}/.skill-path``."""
    sp = Path(specback_dir) / ".skill-path"
    if sp.exists():
        return Path(sp.read_text(encoding="utf-8").strip()).resolve()
    # fallback: sibling of `scripts/`
    return Path(__file__).resolve().parent.parent


def _script_path(name: str) -> Path:
    """Resolve path to a sibling script under the skill's ``scripts/`` dir."""
    return Path(__file__).resolve().parent / name


def _run_script(
    script_name: str,
    args: list[str],
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run one of the sibling scripts with ``args``."""
    script = _script_path(script_name)
    cmd = [sys.executable, str(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def coverage_mece(
    specback_dir: str = ".specback",
    output_dir: str = ".",
    target_dir_for_required: str = ".specback/drafts",
    **extra: str,
) -> GateReport:
    """Check coverage, REF counts, MECE, Question-Bank integrity, etc.

    Wraps ``coverage-check.py``.  Requires ``--output-format json`` so the
    gate can extract per-check granularity from the JSON output.

    Parameters
    ----------
    specback_dir:
        Path to ``.specback/`` (default ``.specback``).
    output_dir:
        Output directory for spec files.
    target_dir_for_required:
        Subdirectory under ``output_dir`` (e.g. ``drafts``).
    **extra:
        Additional CLI flags forwarded verbatim (e.g. ``min_refs_per_chapter=8``).

    Returns
    -------
    GateReport
    """
    report = GateReport(name="coverage_mece")
    args = [
        "--specback-dir", specback_dir,
        "--output-dir", output_dir,
        "--target-dir-for-required", target_dir_for_required,
        "--output-format", "json",
    ]
    for k, v in extra.items():
        args.append(f"--{k.replace('_', '-')}")
        args.append(str(v))

    try:
        proc = _run_script("coverage-check.py", args, timeout=300)
    except subprocess.TimeoutExpired:
        report.check("script: coverage-check.py", False,
                     "timed out after 300 s")
        return report

    # Exit code 2 = missing required artifacts (structural failure).
    if proc.returncode == 2:
        report.check("required artifacts exist", False,
                     proc.stderr.strip()[:512] or "missing inventory / trace")
        return report

    # Try to parse JSON output for per-check granularity.
    data: dict[str, Any] = {}
    try:
        data = json.loads(proc.stdout, parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, ValueError):
        pass

    gate_failures: list[str] = data.get("gate_failures", [])
    missing: list[str] = data.get("missing_required", [])

    # If we have structured per-check data, use it.
    if gate_failures or missing:
        for f in missing:
            report.check(f"required: {f}", False, "file not found")
        for f in gate_failures:
            report.check(f, False, "")
        # Derive a synthetic "setup ok" check so the count includes
        # whatever *did* work.
        setup_bare_min = (
            data.get("total_inventory", 1) > 0
            if "total_inventory" in data
            else True
        )
        report.check("script: coverage-check.py ran", setup_bare_min,
                     f"exit code {proc.returncode}")
        return report

    # No structured failures but non-zero exit → generic failure.
    if proc.returncode != 0:
        report.check("coverage-check.py exit 0", False,
                     f"exit code {proc.returncode}: {proc.stderr.strip()[:300]}")
        return report

    # Everything passed — record derived stats as positive checks.
    report.check("script: coverage-check.py", True, "exit 0")
    for key in ("total_inventory", "questions_total", "drafts_scanned"):
        if key in data:
            report.check(f"{key}: {data[key]}", True, "")
    if "mece_coverage_rate" in data:
        rate = data["mece_coverage_rate"]
        # MECE gate is threshold-based (--min-mece-coverage), matching
        # coverage-check.py itself. `mece_passed_strict` (complete coverage) is
        # a stricter, optional signal — NOT the gate criterion (Issue #376 /
        # SB-05) so trace / coverage / gate all agree on the same threshold.
        threshold = float(extra.get("min_mece_coverage", "0.7"))
        ok = rate >= threshold
        report.check("MECE coverage", ok, f"{rate:.0%} >= {threshold:.0%}")
    ch_count = len(data.get("chapter_metrics", []))
    if ch_count:
        report.check(f"chapters checked: {ch_count}", True, "")

    return report


def schema_valid(data_file: str, schema_path: str) -> GateReport:
    """Validate a JSON data file against its JSON Schema file.

    Wraps ``validate-schema.py``.  The script outputs human-readable
    messages to stderr; the gate parses those for violation details.

    Parameters
    ----------
    data_file:
        Path to the data JSON file (e.g. ``.specback/goal.json``).
    schema_path:
        Path to the JSON Schema file (e.g. ``schemas/goal.schema.json``).

    Returns
    -------
    GateReport
    """
    report = GateReport(name="schema_valid")
    args = ["--schema", schema_path, "--data-file", data_file]

    try:
        proc = _run_script("validate-schema.py", args, timeout=60)
    except subprocess.TimeoutExpired:
        report.check("script: validate-schema.py", False,
                     "timed out after 60 s")
        return report

    if proc.returncode == 2:
        report.check("script invocation", False,
                     proc.stderr.strip()[:512])
        return report

    # Parse stderr for violation lines: "   • <msg>"
    violations: list[str] = []
    for line in proc.stderr.splitlines():
        striped = line.strip()
        if striped.startswith("•"):
            violations.append(striped.lstrip("• "))

    if violations:
        for v in violations:
            report.check(v, False, "")
    else:
        ok = proc.returncode == 0
        report.check(
            "schema validation",
            ok,
            "all constraints satisfied" if ok else f"exit code {proc.returncode}",
        )

    return report


def traceability_full(
    specback_dir: str = ".specback",
    output_dir: str = ".",
    target_dir: str = ".specback/drafts",
) -> GateReport:
    """Verify traceability data — trace.json exists and is structurally
    sound.

    Runs ``build-trace.py`` to (re)generate ``trace.json`` (canonical
    location ``{specback-dir}/trace.json``), then validates the output
    artifact.

    Parameters
    ----------
    specback_dir:
        Path to ``.specback/``.
    output_dir:
        Output directory (default: same as specback parent).
    target_dir:
        Directory to scan for REF markers (drafts/final relative to
        specback_dir, or an absolute chapter dir). Passed through to
        build-trace.py as ``--target-dir-for-required`` (Issue #378 / SB-07).

    Returns
    -------
    GateReport
    """
    report = GateReport(name="traceability_full")
    args = ["--specback-dir", specback_dir]
    if target_dir:
        # target_dir is passed through as build-trace.py's
        # --target-dir-for-required: a bare drafts/final token resolves
        # relative to specback_dir, an absolute path is scanned as-is
        # (Issue #378 / SB-07). No re-resolution needed here.
        args += ["--target-dir-for-required", target_dir]

    try:
        proc = _run_script("build-trace.py", args, timeout=120)
    except subprocess.TimeoutExpired:
        report.check("script: build-trace.py", False, "timed out after 120 s")
        return report

    ok_exit = proc.returncode == 0
    report.check("build-trace.py exit 0", ok_exit,
                 f"exit code {proc.returncode}" if not ok_exit
                 else f"stderr: {proc.stderr.strip()[:200]}")

    # Validate trace.json.
    trace_path = Path(specback_dir) / "trace.json"
    if not trace_path.exists():
        report.check("trace.json exists", False, f"not found at {trace_path}")
        return report

    try:
        trace: dict[str, Any] = json.loads(
            trace_path.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        report.check("trace.json parses", False, str(exc))
        return report

    # Structural checks.
    by_src = trace.get("by_source", {})
    by_sec = trace.get("by_section", {})
    report.check("by_source entries", bool(by_src), f"{len(by_src)} source(s)")
    report.check("by_section entries", bool(by_sec), f"{len(by_sec)} section(s)")

    total = trace.get("source_units_total", 0)
    covered = trace.get("source_units_covered", 0)
    mece = trace.get("mece_passed")
    if mece is not None:
        report.check("MECE passed", mece, f"{covered}/{total} covered")
    report.check("schema_version present",
                 "schema_version" in trace,
                 trace.get("schema_version", "missing"))

    return report


def drift_detected(
    specback_dir: str = ".specback",
    output_dir: str = ".",
    target_dir: str = ".specback/drafts",
) -> GateReport:
    """Run drift detection and verify the report artefacts were generated.

    Wraps ``detect-drift.py``.

    Parameters
    ----------
    specback_dir:
        Path to ``.specback/``.
    output_dir:
        Output directory for drift-report files.

    Returns
    -------
    GateReport
    """
    report = GateReport(name="drift_detected")
    # --json is required so drift-report.json is written on every run —
    # including the zero-changes case (Issue #256). Without it the gate
    # would look for a JSON file that only exists when prior runs wrote it.
    args = ["--specback-dir", specback_dir, "--output-dir", output_dir, "--json"]

    try:
        proc = _run_script("detect-drift.py", args, timeout=120)
    except subprocess.TimeoutExpired:
        report.check("script: detect-drift.py", False, "timed out after 120 s")
        return report

    report.check("detect-drift.py exit 0", proc.returncode == 0,
                 f"exit code {proc.returncode}")

    # Verify output artefacts.
    report_md = Path(output_dir) / "drift-report.md"
    report_json = Path(output_dir) / "drift-report.json"

    report.check("drift-report.md exists", report_md.exists(),
                 str(report_md) if report_md.exists() else "missing")
    report.check("drift-report.json exists", report_json.exists(),
                 str(report_json) if report_json.exists() else "missing")

    if report_json.exists():
        try:
            drift: dict[str, Any] = json.loads(
                report_json.read_text(encoding="utf-8"),
                parse_constant=reject_nonfinite,
            )
            affected = drift.get("affected_sections") or drift.get("sections") or []
            report.check("affected sections reviewed", True,
                         f"{len(affected)} section(s) potentially affected")
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            report.check("drift-report.json parses", False, str(exc))

    return report


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------

GATE_REGISTRY: dict[str, Any] = {
    "coverage_mece": coverage_mece,
    "schema_valid": schema_valid,
    "traceability_full": traceability_full,
    "drift_detected": drift_detected,
}


def run_gates(
    *names: str,
    specback_dir: str = ".specback",
    output_dir: str = ".",
    **kwargs: str,
) -> list[GateReport]:
    """Run one or more gates and return their reports.

    Parameters
    ----------
    *names:
        Gate names to run.  When empty, runs **all** registered gates.

    Returns
    -------
    list[GateReport]
        One report per gate, in registration order.
    """
    targets = names or list(GATE_REGISTRY)
    reports: list[GateReport] = []
    for name in targets:
        fn = GATE_REGISTRY[name]
        if name == "coverage_mece":
            reports.append(fn(specback_dir=specback_dir, output_dir=output_dir, **kwargs))
        elif name == "schema_valid":
            dp = kwargs.get("data_file", "")
            sp = kwargs.get("schema", "")
            reports.append(fn(data_file=dp, schema_path=sp))
        elif name in ("traceability_full", "drift_detected"):
            reports.append(fn(specback_dir=specback_dir, output_dir=output_dir,
                              target_dir=kwargs.get("target_dir", ".specback/drafts")))
        else:
            reports.append(fn(specback_dir=specback_dir, output_dir=output_dir))
    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="specback Gate runner — run verification checks",
    )
    p.add_argument("--gate", choices=list(GATE_REGISTRY), required=True,
                   help="Gate to run")
    p.add_argument("--specback-dir", default=".specback",
                   help="Path to .specback/ directory")
    p.add_argument("--output-dir", default=".",
                   help="Output directory for spec files")
    p.add_argument("--target-dir", default=".specback/drafts",
                   help="Target directory for required artifacts")
    p.add_argument("--schema", default="",
                   help="Path to JSON Schema file (schema_valid gate)")
    p.add_argument("--data-file", default="",
                   help="Path to data JSON file (schema_valid gate)")
    p.add_argument("--json", action="store_true", default=True,
                   help="Output report as JSON (default)")
    p.add_argument("--text", action="store_true",
                   help="Output report as human-readable text")

    args = p.parse_args(argv)
    fn = GATE_REGISTRY[args.gate]

    if args.gate == "coverage_mece":
        report = fn(specback_dir=args.specback_dir,
                     output_dir=args.output_dir,
                     target_dir_for_required=args.target_dir)
    elif args.gate == "schema_valid":
        report = fn(data_file=args.data_file, schema_path=args.schema)
    elif args.gate == "traceability_full":
        report = fn(specback_dir=args.specback_dir,
                    output_dir=args.output_dir,
                    target_dir=args.target_dir)
    else:
        report = fn(specback_dir=args.specback_dir,
                    output_dir=args.output_dir)

    if args.text:
        print(report.summary)
        if not report.passed:
            print("Failures:")
            for f in report.failures:
                print(f"  ✗ {f['item']}: {f['note']}")
    else:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
