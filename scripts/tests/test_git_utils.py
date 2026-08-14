"""
Unit tests for git_utils.resolve_ref (Issue #253 — git base argument injection).

Verifies that option-like values (``--output=...``) and other unsafe refs are
rejected BEFORE they reach a git command, and that legitimate refs resolve to
a commit hash.
"""

from __future__ import annotations

import json
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
# run_git_diff — shared git diff subprocess wrapper (Issue #282)
# ---------------------------------------------------------------------------


def test_run_git_diff_name_status_matches_raw_git(tmp_path: Path) -> None:
    """Output must be byte-identical to a raw ``git diff --name-status``."""
    repo = _init_repo(tmp_path)
    (repo / "sample.py").write_text("x = 2\n", encoding="utf-8")
    actual = git_utils.run_git_diff("HEAD", "--name-status", cwd=repo)
    expected = subprocess.run(
        ["git", "diff", "--name-status", "HEAD"],
        capture_output=True, text=True, cwd=repo,
    ).stdout
    assert actual == expected
    assert "M\tsample.py" in actual


def test_run_git_diff_unified_zero_context_matches_raw_git(tmp_path: Path) -> None:
    """``-U0`` mode (fix-refs.py contract) must match the raw git output."""
    repo = _init_repo(tmp_path)
    (repo / "sample.py").write_text("x = 2\n", encoding="utf-8")
    actual = git_utils.run_git_diff("HEAD", "-U0", cwd=repo)
    expected = subprocess.run(
        ["git", "diff", "-U0", "HEAD"],
        capture_output=True, text=True, cwd=repo,
    ).stdout
    assert actual == expected
    assert actual.startswith("diff --git a/sample.py b/sample.py")


def test_run_git_diff_rejects_option_like_base(tmp_path: Path) -> None:
    """Option-like base must be rejected before git runs (Issue #253)."""
    repo = _init_repo(tmp_path)
    with pytest.raises(SystemExit):
        git_utils.run_git_diff("--output=/tmp/x", "--name-status", cwd=repo)


def test_run_git_diff_failure_exits_with_error(capsys, tmp_path: Path) -> None:
    """Non-zero git diff exit → ``ERROR: git diff failed:`` + SystemExit."""
    repo = _init_repo(tmp_path)
    (repo / "sample.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        # --exit-code makes git exit 1 when the tree differs
        git_utils.run_git_diff("HEAD", "--exit-code", cwd=repo)
    err = capsys.readouterr().err
    assert "ERROR: git diff failed:" in err


# ---------------------------------------------------------------------------
# parse_diff_name_status — --name-status text parser (Issue #282)
# ---------------------------------------------------------------------------


def test_parse_diff_name_status_basic() -> None:
    text = "M\tapp/models/issue.rb\nA\tspec/issue_spec.rb\nD\told.rb\n"
    assert git_utils.parse_diff_name_status(text) == [
        {"status": "M", "file": "app/models/issue.rb"},
        {"status": "A", "file": "spec/issue_spec.rb"},
        {"status": "D", "file": "old.rb"},
    ]


def test_parse_diff_name_status_rename() -> None:
    text = "R100\told/path.rb\tnew/path.rb\n"
    assert git_utils.parse_diff_name_status(text) == [
        {"status": "R", "file": "new/path.rb", "old_file": "old/path.rb"},
    ]


def test_parse_diff_name_status_copy_uses_new_path() -> None:
    """C(copy) entries have the same 3-field shape as R — file must be the new path."""
    text = "C100\told/path.rb\tnew/path.rb\n"
    assert git_utils.parse_diff_name_status(text) == [
        {"status": "C", "file": "new/path.rb", "old_file": "old/path.rb"},
    ]


def test_parse_diff_name_status_ignores_garbage() -> None:
    text = "\nnot-a-diff\nM\tonly_file.rb\n"
    assert git_utils.parse_diff_name_status(text) == [
        {"status": "M", "file": "only_file.rb"},
    ]


def test_parse_diff_name_status_roundtrip(tmp_path: Path) -> None:
    """run_git_diff --name-status output parses back to entries (detect-drift wiring)."""
    repo = _init_repo(tmp_path)
    (repo / "sample.py").write_text("x = 2\n", encoding="utf-8")
    text = git_utils.run_git_diff("HEAD", "--name-status", cwd=repo)
    entries = git_utils.parse_diff_name_status(text)
    assert {"status": "M", "file": "sample.py"} in entries


# ---------------------------------------------------------------------------
# resolve_base — shared base-ref resolution (Issue #282)
# ---------------------------------------------------------------------------


def test_resolve_base_explicit_arg_wins(tmp_path: Path) -> None:
    assert git_utils.resolve_base("v1.0", tmp_path) == "v1.0"
    assert git_utils.resolve_base("HEAD", tmp_path / ".specback") == "HEAD"


def test_resolve_base_uses_state_generated_at_commit(tmp_path: Path) -> None:
    specback = tmp_path / ".specback"
    specback.mkdir()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": "deadbeef"}), encoding="utf-8"
    )
    assert git_utils.resolve_base(None, specback) == "deadbeef"


def test_resolve_base_falls_back_to_head(tmp_path: Path) -> None:
    specback = tmp_path / ".specback"
    specback.mkdir()
    assert git_utils.resolve_base(None, specback) == "HEAD"


def test_resolve_base_missing_state_dir_falls_back_to_head(tmp_path: Path) -> None:
    assert git_utils.resolve_base(None, tmp_path / "no-such-dir") == "HEAD"


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
