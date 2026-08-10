"""Smoke + security regression tests for detect-drift.py (Phase 7 — Drift Detection)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "detect-drift.py"


def test_help_includes_specback_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--specback-dir" in result.stdout


def test_help_includes_output_dir():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--output-dir" in result.stdout


def test_help_includes_mode():
    """--mode auto/git/hash appears in help."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout
    assert "auto" in result.stdout
    assert "git" in result.stdout
    assert "hash" in result.stdout


def test_help_includes_json():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--json" in result.stdout


def test_args_with_output_dir():
    """--output-dir combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", "/tmp/x", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_args_with_mode_hash():
    """--mode hash combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "hash", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Security regression (Issue #253 — git base argument injection)
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit and a minimal .specback dir."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
        env={"GIT_AUTHOR_DATE": "2026-01-01T00:00:00",
             "GIT_COMMITTER_DATE": "2026-01-01T00:00:00"},
    )
    specback = tmp_path / ".specback"
    specback.mkdir()
    (specback / "source-map.json").write_text(
        json.dumps({"units": [], "stats": {}, "target_root": "."}),
        encoding="utf-8",
    )
    (specback / "trace.json").write_text("{}", encoding="utf-8")
    return specback


def test_state_json_injection_rejected(tmp_path):
    """state.json generated_at_commit must not be passed to git as an option."""
    specback = _init_repo(tmp_path)
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": "--output=.specback/pwned.txt"}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback), "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid git ref" in result.stderr
    assert not (specback / "pwned.txt").exists()


def test_cli_base_option_injection_rejected(tmp_path):
    """--base='--output=...' must be rejected, not executed."""
    specback = _init_repo(tmp_path)
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--specback-dir", str(specback),
            "--mode", "git",
            "--base=--output=.specback/pwned.txt",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid git ref" in result.stderr
    assert not (specback / "pwned.txt").exists()


def test_state_json_valid_commit_ok(tmp_path):
    """A real commit hash in state.json still works."""
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback), "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "No changes detected" in result.stdout


def test_json_written_on_no_changes(tmp_path):
    """--json must produce drift-report.json even with zero changes (Issue #256).

    Gates consume drift-report.json; without this, the file only exists
    when a prior run recorded changes, making the gate depend on history.
    """
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "git", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"detect-drift failed:\n{result.stderr}"
    json_path = specback / "drift-report.json"
    assert json_path.exists(), "drift-report.json not written on no-changes path"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["changed_files"] == 0
