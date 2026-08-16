"""Tests for the shared conftest helpers (Issue #324).

These pin the behavior of :func:`conftest.create_repo`,
:func:`conftest.add_scripts_to_path`, :func:`conftest.load_script_module` and
the :func:`conftest.init_repo_factory` fixture, so the de-duplicated helpers
keep working as the suite grows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import add_scripts_to_path, create_repo, load_script_module

SCRIPTS = Path(__file__).resolve().parent.parent


def test_create_repo_makes_committed_git_repo(tmp_path: Path) -> None:
    repo = create_repo(tmp_path, with_spec=False)
    assert repo == tmp_path
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert len(head) == 40
    assert (repo / "sample.py").exists()


def test_create_repo_dates_are_deterministic(tmp_path: Path) -> None:
    create_repo(tmp_path, with_spec=False)
    date = subprocess.run(
        ["git", "log", "-1", "--format=%aI"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert date.startswith("2026-01-01T00:00:00")


def test_create_repo_with_spec_returns_specback(tmp_path: Path) -> None:
    specback = create_repo(tmp_path, with_spec=True)
    assert specback == tmp_path / ".specback"
    assert (specback / "source-map.json").exists()
    assert (specback / "trace.json").exists()
    assert (specback / "drafts" / "01-overview.md").exists()


def test_create_repo_no_spec_still_scaffolds_specback(tmp_path: Path) -> None:
    repo = create_repo(tmp_path, with_spec=False)
    assert (repo / ".specback" / "drafts").is_dir()
    assert not (repo / ".specback" / "source-map.json").exists()


def test_add_scripts_to_path_restores_syspath() -> None:
    before = list(sys.path)
    with add_scripts_to_path(SCRIPTS):
        assert str(SCRIPTS.resolve()) in sys.path
    assert sys.path == before


def test_add_scripts_to_path_does_not_duplicate_when_present() -> None:
    already = str(SCRIPTS.resolve())
    # Normalize so we start at exactly one pre-existing entry.
    while already in sys.path:
        sys.path.remove(already)
    sys.path.insert(0, already)
    count_before = sum(1 for p in sys.path if p == already)
    with add_scripts_to_path(SCRIPTS):
        count_inside = sum(1 for p in sys.path if p == already)
    sys.path.remove(already)
    assert count_inside == count_before == 1


def test_load_script_module_registers_and_cleans_path() -> None:
    before = list(sys.path)
    mod = load_script_module(SCRIPTS / "change-spec.py", "change_spec_probe")
    assert mod is sys.modules.get("change_spec_probe")
    assert sys.path == before  # no leaked sys.path mutation


def test_init_repo_factory(tmp_path: Path, init_repo_factory) -> None:
    repo = init_repo_factory(tmp_path, with_spec=False)
    assert repo == tmp_path
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).returncode == 0
