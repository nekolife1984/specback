"""Tests for specback-gate.py — thin CI drift wrapper (Issue #266).

Covers:
- CLI surface (--help)
- base resolution: explicit --base, CI mode (GITHUB_BASE_REF), local mode
- verdict classification (pass / warn / fail / warn-only downgrade)
- E2E: spec-not-generated skip, drift-detected fail, clean pass
- security: malicious --base rejected via git_utils.resolve_ref
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import create_repo as _create_repo, load_script_module

SCRIPT = Path(__file__).resolve().parent.parent / "specback-gate.py"

# Import specback-gate.py as a module for pure-function tests (Issue #258),
# without leaking scripts/ onto sys.path (Issue #324).
gate = load_script_module(SCRIPT, "specback_gate_core")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path, with_spec: bool = True) -> Path:
    """Create a git repo with one commit and a minimal .specback dir.

    Returns the .specback path. When ``with_spec`` is True, writes
    source-map.json + trace.json with a single unit (SRC-0001) mapped to
    a spec section; the repo also has a spec draft file.
    """
    return _create_repo(tmp_path, with_spec=with_spec)


def _run_gate(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run specback-gate.py against *tmp_path* repo."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir",
         str(tmp_path / ".specback"), *extra],
        capture_output=True, text=True, cwd=tmp_path,
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_help_includes_options():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    for opt in ("--specback-dir", "--base", "--ci", "--json", "--warn-only"):
        assert opt in result.stdout


def test_missing_specback_dir_is_usage_error(tmp_path):
    result = _run_gate(tmp_path)
    assert result.returncode == 2
    assert "is not a directory" in result.stderr


# ---------------------------------------------------------------------------
# parse_args / run_script / load_drift_report / resolve_merge_base
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = gate.parse_args([])
    assert args.specback_dir == Path(".specback")
    assert args.base is None
    assert args.ci is False
    assert args.json is False
    assert args.warn_only is False


def test_parse_args_flags():
    args = gate.parse_args(["--ci", "--json", "--warn-only",
                            "--base", "main", "--specback-dir", "sb"])
    assert args.ci is True
    assert args.json is True
    assert args.warn_only is True
    assert args.base == "main"
    assert args.specback_dir == Path("sb")


def test_run_script_help(tmp_path):
    """run_script() shells out to a sibling script; --help returns 0."""
    proc = gate.run_script("detect-drift.py", ["--help"])
    assert proc.returncode == 0
    assert "--specback-dir" in proc.stdout


def test_load_drift_report_missing_returns_empty(tmp_path):
    assert gate.load_drift_report(tmp_path) == {}


def test_load_drift_report_parses(tmp_path):
    (tmp_path / "drift-report.json").write_text(
        '{"summary": {"changed_files": 2}}', encoding="utf-8",
    )
    report = gate.load_drift_report(tmp_path)
    assert report["summary"]["changed_files"] == 2


def test_resolve_merge_base_returns_sha(tmp_path):
    specback = _init_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True)
    mb = gate.resolve_merge_base("HEAD", cwd=str(specback.parent))
    assert mb == head.stdout.strip()


def test_resolve_merge_base_missing_ref_is_none(tmp_path):
    """Missing ref returns None silently (no ERROR noise)."""
    specback = _init_repo(tmp_path)
    assert gate.resolve_merge_base("origin/nope", cwd=str(specback.parent)) \
        is None


def test_resolve_merge_base_rejects_option_like(tmp_path):
    specback = _init_repo(tmp_path)
    assert gate.resolve_merge_base("--output=/tmp/x",
                                   cwd=str(specback.parent)) is None


# ---------------------------------------------------------------------------
# Verdict classification (pure functions)
# ---------------------------------------------------------------------------


def _drift(affected: int = 0, deleted: int = 0, new_uncovered: int = 0):
    return {"summary": {
        "affected_spec_sections": affected,
        "deleted_sources_with_refs": deleted,
        "new_uncovered_sources": new_uncovered,
    }}


def test_verdict_pass():
    assert gate.evaluate_verdict(_drift(), 0, 0, False) == "pass"


def test_verdict_fail_on_affected():
    assert gate.evaluate_verdict(_drift(affected=2), 0, 0, False) == "fail"


def test_verdict_fail_on_deleted():
    assert gate.evaluate_verdict(_drift(deleted=1), 0, 0, False) == "fail"


def test_verdict_warn_on_orphaned_refs():
    assert gate.evaluate_verdict(_drift(), 3, 0, False) == "warn"


def test_verdict_warn_on_new_uncovered():
    assert gate.evaluate_verdict(_drift(), 0, 1, False) == "warn"


def test_verdict_warn_only_downgrades_fail():
    assert gate.evaluate_verdict(_drift(affected=1), 0, 0, True) == "pass"


# ---------------------------------------------------------------------------
# Base resolution
# ---------------------------------------------------------------------------


def test_resolve_base_explicit(tmp_path):
    specback = _init_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True)
    resolved = gate.resolve_base(head.stdout.strip(), False,
                                 cwd=str(specback.parent))
    assert resolved == head.stdout.strip()


def test_resolve_base_ci_uses_github_base_ref(tmp_path):
    specback = _init_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True)
    # Create origin/main pointing at HEAD so merge-base resolves.
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", tmp_path],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=tmp_path, check=True)
    old = os.environ.get("GITHUB_BASE_REF")
    os.environ["GITHUB_BASE_REF"] = "main"
    try:
        resolved = gate.resolve_base(None, True, cwd=str(specback.parent))
    finally:
        if old is None:
            os.environ.pop("GITHUB_BASE_REF", None)
        else:
            os.environ["GITHUB_BASE_REF"] = old
    assert resolved == head.stdout.strip()


def test_resolve_base_ci_falls_back_to_origin_main(tmp_path):
    specback = _init_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", tmp_path],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=tmp_path, check=True)
    old = os.environ.get("GITHUB_BASE_REF")
    os.environ.pop("GITHUB_BASE_REF", None)
    try:
        resolved = gate.resolve_base(None, True, cwd=str(specback.parent))
    finally:
        if old is not None:
            os.environ["GITHUB_BASE_REF"] = old
    assert resolved == head.stdout.strip()


def test_resolve_base_rejects_injection(tmp_path):
    """--base=--output=/tmp/x must be rejected, not passed to git."""
    specback = _init_repo(tmp_path)
    try:
        gate.resolve_base("--output=/tmp/pwned", False,
                          cwd=str(specback.parent))
        assert False, "expected SystemExit"
    except SystemExit:
        pass
    assert not Path("/tmp/pwned").exists()


# ---------------------------------------------------------------------------
# E2E: spec-not-generated skip
# ---------------------------------------------------------------------------


def test_e2e_skip_when_spec_missing(tmp_path):
    """No source-map.json -> warn (exit 0), not a CI failure."""
    _init_repo(tmp_path, with_spec=False)
    result = _run_gate(tmp_path, "--json")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["verdict"] == "warn"
    assert any("spec artifacts missing" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# E2E: drift detection
# ---------------------------------------------------------------------------


def test_e2e_drift_fails(tmp_path):
    """Modifying sample.py after spec generation -> fail (exit 1)."""
    _init_repo(tmp_path)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                              capture_output=True, text=True, check=True)
    # Introduce a change on top of the base commit.
    (tmp_path / "sample.py").write_text("x = 2\n", encoding="utf-8")

    result = _run_gate(tmp_path, "--base", base_sha.stdout.strip(), "--json")
    assert result.returncode == 1, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["verdict"] == "fail"
    assert out["drift"]["summary"]["changed_files"] >= 1


def test_e2e_drift_warn_only(tmp_path):
    """--warn-only downgrades fail -> exit 0."""
    _init_repo(tmp_path)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                              capture_output=True, text=True, check=True)
    (tmp_path / "sample.py").write_text("x = 2\n", encoding="utf-8")

    result = _run_gate(tmp_path, "--base", base_sha.stdout.strip(),
                       "--warn-only", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["verdict"] == "pass"  # warn-only downgrades fail -> pass


def test_e2e_clean_pass(tmp_path):
    """No changes -> pass (exit 0), drift report still generated."""
    _init_repo(tmp_path)
    result = _run_gate(tmp_path, "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["verdict"] == "pass"
    assert (tmp_path / ".specback" / "drift-report.json").exists()


# ---------------------------------------------------------------------------
# fix-refs contract mismatch (#313): a broken fix-refs --json output must
# NOT be swallowed as "0 orphaned / ok".
# ---------------------------------------------------------------------------


def _run_gate_with_fake_fix_refs(
    tmp_path: Path,
    monkeypatch,
    stdout: str,
    returncode: int,
) -> Path:
    specback = _init_repo(tmp_path)
    real_run_script = gate.run_script

    def fake_run_script(name, args, cwd=None, timeout=120):
        if name == "fix-refs.py":
            return subprocess.CompletedProcess(
                args, returncode, stdout=stdout, stderr="boom"
            )
        return real_run_script(name, args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(gate, "run_script", fake_run_script)
    return specback


def test_fix_refs_non_json_output_is_warned(tmp_path, monkeypatch, capsys):
    """fix-refs printing garbage -> warning + ok=False, NOT silent ok."""
    specback = _run_gate_with_fake_fix_refs(
        tmp_path, monkeypatch, stdout="not json", returncode=0
    )
    rc = gate.main(["--specback-dir", str(specback), "--json"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert rc == 0  # warning only; gate does not hard-fail
    assert out["fix_refs"]["ok"] is False
    assert any("unparseable output" in w for w in out["warnings"])


def test_fix_refs_nonzero_exit_is_warned(tmp_path, monkeypatch, capsys):
    """fix-refs exiting non-zero -> warning + ok=False, NOT silent ok."""
    specback = _run_gate_with_fake_fix_refs(
        tmp_path, monkeypatch, stdout="{}", returncode=2
    )
    rc = gate.main(["--specback-dir", str(specback), "--json"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert rc == 0  # warning only; gate does not hard-fail
    assert out["fix_refs"]["ok"] is False
    assert any("fix-refs.py --check failed" in w for w in out["warnings"])
