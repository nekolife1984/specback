"""
Unit tests for git_utils.resolve_ref (Issue #253 — git base argument injection).

Verifies that option-like values (``--output=...``) and other unsafe refs are
rejected BEFORE they reach a git command, and that legitimate refs resolve to
a commit hash.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add the scripts directory to sys.path so we can import git_utils
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import git_utils


# ---------------------------------------------------------------------------
# Rejection tests (no git repo needed — validation happens first)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        "--output=/tmp/x",            # git option injection
        "--output=.git/config",       # would truncate .git/config
        "--output=README.md",         # would truncate an existing file
        "-x",                          # plain option
        "--",                          # end-of-options marker
        "",                            # empty string
        "main; rm -rf /",              # shell metacharacters
        "HEAD --output=/tmp/x",        # embedded option
        "..",                          # path traversal-ish
    ],
)
def test_resolve_ref_rejects_option_like_base(malicious: str) -> None:
    with pytest.raises(SystemExit):
        git_utils.resolve_ref(malicious)


def test_resolve_ref_rejects_invalid_ref_in_git_repo(tmp_path: Path) -> None:
    """Even inside a real git repo, option-like bases are rejected."""
    _init_repo(tmp_path)
    with pytest.raises(SystemExit):
        git_utils.resolve_ref("--output=/tmp/x", cwd=tmp_path)


# ---------------------------------------------------------------------------
# Resolution tests (real git repo)
# ---------------------------------------------------------------------------


def test_resolve_ref_resolves_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    resolved = git_utils.resolve_ref("HEAD", cwd=repo)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=repo,
    ).stdout.strip()
    assert resolved == expected
    assert len(resolved) == 40


def test_resolve_ref_resolves_short_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    full = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=repo,
    ).stdout.strip()
    short = full[:8]
    assert git_utils.resolve_ref(short, cwd=repo) == full


def test_resolve_ref_rejects_unresolvable_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    with pytest.raises(SystemExit):
        git_utils.resolve_ref("no-such-ref-xyz", cwd=repo)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit and return its path."""
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
    return tmp_path
