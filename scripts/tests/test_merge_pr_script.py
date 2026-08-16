"""Tests for scripts/merge-pr.sh using a mocked `gh` CLI.

The mock gh is a shell script placed on PATH; MOCK_MODE controls its behavior:
  pass          -> all checks pass (exit 0)
  fail          -> one check fails (exit 1, JSON with bucket=fail)
  fetch_error   -> gh itself errors (exit 1, non-JSON stderr message)
  pending       -> first check call returns pending (exit 8); after --watch
                   (and on subsequent calls) it returns pass
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MERGE_PR_SH = ROOT / "scripts" / "merge-pr.sh"

MOCK_GH = """#!/bin/sh
# Mock gh for merge-pr.sh tests. Behavior is controlled by MOCK_MODE.
MOCK_DIR="$MOCK_WORKDIR"
STATE_FILE="$MOCK_DIR/state"

case "$1/$2" in
  pr/checks)
    HAS_WATCH=0
    for a in "$@"; do [ "$a" = "--watch" ] && HAS_WATCH=1; done
    case "$MOCK_MODE" in
      pass)
        echo '[{"bucket":"pass","name":"test (3.11)","state":"SUCCESS"},{"bucket":"pass","name":"test (3.12)","state":"SUCCESS"}]'
        exit 0 ;;
      fail)
        echo '[{"bucket":"fail","name":"test (3.11)","state":"FAILURE"},{"bucket":"pass","name":"test (3.12)","state":"SUCCESS"}]'
        exit 1 ;;
      fetch_error)
        echo 'GraphQL: Could not resolve to a PullRequest with the number of 123.' >&2
        exit 1 ;;
      pending)
        if [ "$HAS_WATCH" = "1" ]; then
          echo "1" > "$STATE_FILE"
          echo '[{"bucket":"pass","name":"test (3.11)","state":"SUCCESS"}]'
          exit 0
        fi
        if [ -f "$STATE_FILE" ]; then
          echo '[{"bucket":"pass","name":"test (3.11)","state":"SUCCESS"}]'
          exit 0
        fi
        echo '[{"bucket":"pending","name":"test (3.11)","state":"IN_PROGRESS"}]'
        exit 8 ;;
    esac
    ;;
  pr/merge)
    echo "merge called" > "$MOCK_DIR/merge.log"
    exit 0 ;;
esac
exit 0
"""


@pytest.fixture()
def mock_env(tmp_path):
    """Create a mock gh on PATH and return a dict of environment overrides."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    gh = mock_bin / "gh"
    gh.write_text(MOCK_GH)
    gh.chmod(0o755)

    env = os.environ.copy()
    env["MOCK_MODE"] = "pass"
    env["MOCK_WORKDIR"] = str(tmp_path)
    env["PATH"] = str(mock_bin) + os.pathsep + env.get("PATH", "")
    return env, tmp_path


def run_script(env, *args):
    return subprocess.run(
        ["sh", str(MERGE_PR_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )


def test_all_checks_pass_merges(mock_env):
    env, tmp = mock_env
    env["MOCK_MODE"] = "pass"
    result = run_script(env, "1")
    assert result.returncode == 0
    assert "All CI checks passed" in result.stdout
    assert (tmp / "merge.log").exists(), "gh pr merge should have been called"


def test_failed_check_blocks_merge(mock_env):
    env, tmp = mock_env
    env["MOCK_MODE"] = "fail"
    result = run_script(env, "1")
    assert result.returncode == 1
    assert "CI checks are FAILING" in result.stdout
    assert "test (3.11)" in result.stdout, "failing check name should be shown"
    assert not (tmp / "merge.log").exists(), "merge must NOT run on failure"


def test_pending_then_pass_waits_then_merges(mock_env):
    env, tmp = mock_env
    env["MOCK_MODE"] = "pending"
    result = run_script(env, "1")
    assert result.returncode == 0
    assert "still pending" in result.stdout, "script should report pending state"
    assert "All CI checks passed" in result.stdout
    assert (tmp / "merge.log").exists(), "merge should run after waiting"


def test_fetch_error_reported_distinctly(mock_env):
    env, tmp = mock_env
    env["MOCK_MODE"] = "fetch_error"
    result = run_script(env, "1")
    assert result.returncode == 1
    assert "Failed to fetch checks" in result.stdout
    assert "FAILING" not in result.stdout, "fetch error must not be shown as CI failure"
    assert not (tmp / "merge.log").exists()
