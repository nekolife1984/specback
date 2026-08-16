"""Shared fixtures/helpers for the ``scripts/tests`` suite (Issue #324).

This module centralizes two concerns that were previously duplicated across
many test files:

``_init_repo`` (git-repo bootstrap)
    Four test files each defined their own ``_init_repo`` with slightly
    different signatures / returned paths.  :func:`create_repo` is the single
    shared implementation; :func:`init_repo_factory` exposes it through the
    fixture mechanism that the issue prefers.

``sys.path`` mutations
    Several test files did an unguarded module-level
    ``sys.path.insert(0, str(SCRIPT.parent))`` to import a ``scripts/``
    module via ``importlib`` and never restored ``sys.path`` afterward.  That
    is a latent test-isolation hazard.  :func:`add_scripts_to_path` is a
    context manager that always restores ``sys.path``, and
    :func:`load_script_module` wraps the common ``spec_from_file_location`` +
    ``exec_module`` dance so the script's parent dir is only on ``sys.path``
    during exec.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

# Deterministic commit dates so commit hashes / drift reports are reproducible
# (mirrors the pattern already used by the pre-existing _init_repo helpers).
GIT_DATES = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00",
}

DRAFT_OVERVIEW = "# Overview\n\nNothing here yet.\n"

SOURCE_MAP: dict[str, Any] = {
    "schema_version": "0.1.0",
    "target_root": ".",
    "units": [
        {
            "id": "SRC-0001",
            "path": "sample.py",
            "kind": "function",
            "name": "main",
            "role": "action",
        },
    ],
    "by_path": {"sample.py": ["SRC-0001"]},
    "by_id": {"SRC-0001": {"id": "SRC-0001", "path": "sample.py"}},
    "stats": {"total_units": 1},
}

TRACE: dict[str, Any] = {
    "schema_version": "0.1.0",
    "source_units_total": 1,
    "source_units_covered": 1,
    "mece_passed": True,
    "by_source": {
        "SRC-0001": {
            "path": "sample.py",
            "covered_by_sections": [
                {"file": "drafts/01-overview.md", "section": "Overview"},
            ],
        },
    },
}


def run_git(
    *args: str,
    cwd: Path | str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` with deterministic commit dates applied."""
    full_env = os.environ.copy()
    full_env.update(GIT_DATES)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        env=full_env,
        capture_output=True,
        text=True,
    )


def create_repo(repo: Path | str, *, with_spec: bool = True) -> Path:
    """Create a git repo at *repo* with one committed ``sample.py``.

    Configures ``user.email`` / ``user.name`` and pins commit dates to
    ``GIT_DATES``.  This is the single shared implementation of the
    previously-duplicated ``_init_repo`` helpers.

    The repo is always scaffolded with a ``.specback/`` + ``.specback/drafts``
    directory (regardless of ``with_spec``).  When ``with_spec`` is True the
    ``.specback`` dir also gets a ``source-map.json``, a ``trace.json`` and a
    draft spec file, and the ``.specback`` path is returned.  When
    ``with_spec`` is False no spec artifacts are written and the repo root is
    returned.
    """
    repo = Path(repo)
    run_git("init", "-q", cwd=repo)
    run_git("config", "user.email", "test@example.com", cwd=repo)
    run_git("config", "user.name", "Test", cwd=repo)
    (repo / "sample.py").write_text("x = 1\n", encoding="utf-8")
    run_git("add", "sample.py", cwd=repo)
    run_git("commit", "-q", "-m", "init", cwd=repo)

    specback = repo / ".specback"
    specback.mkdir(exist_ok=True)
    drafts = specback / "drafts"
    drafts.mkdir(exist_ok=True)
    (drafts / "01-overview.md").write_text(DRAFT_OVERVIEW, encoding="utf-8")
    if with_spec:
        (specback / "source-map.json").write_text(
            json.dumps(SOURCE_MAP), encoding="utf-8"
        )
        (specback / "trace.json").write_text(
            json.dumps(TRACE), encoding="utf-8"
        )
    return specback if with_spec else repo


@pytest.fixture()
def init_repo_factory() -> Callable[..., Path]:
    """Return :func:`create_repo` so tests can request it as a fixture."""

    def _factory(*args: Any, **kwargs: Any) -> Path:
        return create_repo(*args, **kwargs)

    return _factory


@contextmanager
def add_scripts_to_path(scripts_dir: Path | str) -> Iterator[None]:
    """Temporarily add *scripts_dir* to ``sys.path``, always restoring it.

    Safe to nest / call even if the dir is already on ``sys.path`` (no
    duplicate entries, no spurious removals).
    """
    resolved = str(Path(scripts_dir).resolve())
    inserted = False
    try:
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
            inserted = True
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(resolved)
            except ValueError:
                pass  # someone else removed it; nothing to restore


def load_script_module(script_path: Path | str, name: str) -> Any:
    """Load a standalone ``scripts/`` script as a Python module.

    Implements the common importlib dance used across the suite without
    leaking ``sys.path`` mutations:

    * ``spec_from_file_location`` uses an absolute path, so ``scripts/`` does
      NOT need to be on ``sys.path`` to resolve the module itself.
    * The module is registered in ``sys.modules`` BEFORE ``exec_module`` so
      intra-script ``import common`` / ``import git_utils`` resolve against
      the real sibling scripts.
    * The script's parent dir is added to ``sys.path`` only while
      ``exec_module`` runs and is removed in ``finally`` (see
      :func:`add_scripts_to_path`).
    """
    script_path = Path(script_path).resolve()
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None and spec.loader is not None, (
        f"could not build import spec for {script_path}"
    )
    mod = importlib.util.module_from_spec(spec)
    with add_scripts_to_path(script_path.parent):
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod
