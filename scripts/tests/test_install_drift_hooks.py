"""Tests for scripts/install-drift-hooks.sh using a mocked python3.

The mock python3 records its invocations and simulates the drift gate
exit code based on MOCK_GATE_EXIT:
  0  -> pass/warn (no drift)
  1  -> fail (drift detected)
Covers:
- hook file is installed with shebang + chmod +x
- existing pre-push hook is backed up and chained
- warn mode (default): gate fail does NOT block (exit 0)
- fail mode (SPECBACK_FAIL_ON_DRIFT=1): gate fail blocks push (exit 1)
- target is the invocation cwd, not the specback repo
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "scripts" / "install-drift-hooks.sh"

MOCK_PYTHON = """#!/bin/sh
# Mock python3 for install-drift-hooks.sh tests.
MOCK_DIR="$MOCK_WORKDIR"
echo "python3 called with: $*" >> "$MOCK_DIR/python3.log"
exit "${MOCK_GATE_EXIT:-0}"
"""


@pytest.fixture()
def mock_env(tmp_path):
    """Create a mock python3 on PATH and return env + tmp_path."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    py = mock_bin / "python3"
    py.write_text(MOCK_PYTHON)
    py.chmod(0o755)

    env = os.environ.copy()
    env["MOCK_GATE_EXIT"] = "0"
    env["MOCK_WORKDIR"] = str(tmp_path)
    env["PATH"] = str(mock_bin) + os.pathsep + env.get("PATH", "")
    return env, tmp_path


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"],
                   cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "sample.py").write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / ".specback").mkdir()
    (repo / ".specback" / "drafts").mkdir()


def run_install(env, repo):
    return subprocess.run(
        ["sh", str(INSTALL_SH)],
        capture_output=True, text=True, cwd=str(repo), env=env,
    )


def run_hook(repo, env=None):
    return subprocess.run(
        ["sh", str(repo / ".git" / "hooks" / "pre-push"),
         "refs/heads/main", "HEAD", "refs/heads/main", "HEAD"],
        capture_output=True, text=True, cwd=str(repo), env=env,
    )


def test_installs_hook_in_cwd_repo(mock_env, tmp_path):
    """Target is the invocation cwd, never the specback repo."""
    env, _ = mock_env
    repo = tmp_path / "target"
    repo.mkdir()
    _init_repo(repo)
    result = run_install(env, repo)
    assert result.returncode == 0, result.stderr
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    assert os.access(hook, os.X_OK)
    body = hook.read_text(encoding="utf-8")
    assert "specback-drift pre-push hook" in body


def test_backs_up_existing_hook_and_chains(mock_env, tmp_path):
    env, _ = mock_env
    repo = tmp_path / "target"
    repo.mkdir()
    _init_repo(repo)
    hooks = repo / ".git" / "hooks"
    existing = hooks / "pre-push"
    existing.write_text("#!/bin/sh\necho existing-hook-ran\nexit 0\n",
                        encoding="utf-8")
    existing.chmod(0o755)
    # mark with a sentinel so we can detect the backup
    backup_sentinel = hooks / "pre-push.specback-backup"

    result = run_install(env, repo)
    assert result.returncode == 0, result.stderr
    assert backup_sentinel.exists()
    hook = hooks / "pre-push"
    body = hook.read_text(encoding="utf-8")
    # generated wrapper chains the backup
    assert "pre-push.specback-backup" in body


def test_warn_mode_does_not_block_on_drift(mock_env, tmp_path):
    """Default mode: gate exit 1 still allows the push (exit 0)."""
    env, _ = mock_env
    env["MOCK_GATE_EXIT"] = "1"
    repo = tmp_path / "target"
    repo.mkdir()
    _init_repo(repo)
    assert run_install(env, repo).returncode == 0
    hook_result = run_hook(repo, env)
    assert hook_result.returncode == 0, hook_result.stdout + hook_result.stderr


def test_fail_mode_blocks_on_drift(mock_env, tmp_path):
    """SPECBACK_FAIL_ON_DRIFT=1: gate exit 1 blocks the push (exit 1)."""
    env, _ = mock_env
    env["MOCK_GATE_EXIT"] = "1"
    env["SPECBACK_FAIL_ON_DRIFT"] = "1"
    repo = tmp_path / "target"
    repo.mkdir()
    _init_repo(repo)
    assert run_install(env, repo).returncode == 0
    hook_result = run_hook(repo, env)
    assert hook_result.returncode == 1
    assert "drift detected" in hook_result.stdout


def test_gate_invoked_with_ci_and_specback_dir(mock_env, tmp_path):
    env, workdir = mock_env
    repo = tmp_path / "target"
    repo.mkdir()
    _init_repo(repo)
    run_install(env, repo)
    run_hook(repo, env)
    log = (workdir / "python3.log").read_text(encoding="utf-8")
    assert "--ci" in log
    assert "--specback-dir" in log
    assert ".specback" in log


def test_rejects_non_git_dir(mock_env, tmp_path):
    env, _ = mock_env
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_install(env, plain)
    assert result.returncode == 1
    assert "Not a git repository" in result.stderr
