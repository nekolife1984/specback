"""Tests for scripts/specback_install.py — Stamp installer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = ROOT / "scripts" / "specback_install.py"


# ── Helpers ──────────────────────────────────────────────────────────────


def _run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess:
    """Run specback_install.py with given args."""
    return subprocess.run(
        [sys.executable, str(INSTALL_SCRIPT), *args],
        capture_output=True, text=True, timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ─── Tests ──────────────────────────────────────────────────────────────


class TestSmoke:
    """Basic sanity checks."""

    def test_imports(self) -> None:
        """Verify the script can be imported without errors."""
        result = _run("--help")
        assert result.returncode == 0
        assert "Stamp specback into a target" in result.stdout

    def test_nonexistent_target(self, tmp_path: Path) -> None:
        """Verify exit with error when target doesn't exist."""
        result = _run(str(tmp_path / "nonexistent"))
        assert result.returncode == 1
        assert "target not found" in result.stderr


class TestDryRun:
    """--dry-run mode tests."""

    def test_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        """Dry-run should not create any files."""
        result = _run("--dry-run", str(tmp_path))
        assert result.returncode == 0
        assert "Dry-run complete" in result.stdout
        # No files should exist in target
        items = list(tmp_path.iterdir())
        assert len(items) == 0, f"Dry-run created: {items}"

    def test_dry_run_shows_structure(self, tmp_path: Path) -> None:
        """Dry-run should describe what would be stamped."""
        result = _run("--dry-run", str(tmp_path))
        assert result.returncode == 0
        assert ".specback_data/" in result.stdout
        assert ".claude/skills/specback/" in result.stdout
        assert "prompt_engineering/" in result.stdout


class TestInstall:
    """Full install tests."""

    def test_basic_install(self, tmp_path: Path) -> None:
        """Basic stamp install creates expected structure."""
        result = _run(str(tmp_path))
        assert result.returncode == 0, f"Install failed:\n{result.stderr}"
        assert "specback v1.2.0 stamped" in result.stdout

        # Check .specback_data/
        data_dir = tmp_path / ".specback_data"
        assert data_dir.is_dir()
        assert (data_dir / "templates").is_dir()
        assert (data_dir / "prompt_engineering").is_dir()
        assert (data_dir / "llockfile").exists()

        # Check core skill
        skill_dir = tmp_path / ".claude" / "skills" / "specback"
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "phases").is_dir()
        assert (skill_dir / "scripts").is_dir()
        assert (skill_dir / "references").is_dir()
        assert (skill_dir / "templates").is_dir()

        # Check search skill
        assert (tmp_path / ".claude" / "skills" / "specback-search").is_dir()

    def test_second_install_fails(self, tmp_path: Path) -> None:
        """Second install without --force should fail."""
        _run(str(tmp_path))
        result = _run(str(tmp_path))
        assert result.returncode == 1
        assert "already installed" in result.stdout
        assert "Use --force" in result.stdout

    def test_force_install(self, tmp_path: Path) -> None:
        """--force should allow re-install."""
        _run(str(tmp_path))
        result = _run("--force", str(tmp_path))
        assert result.returncode == 0
        assert "specback v1.2.0 stamped" in result.stdout

    def test_lockfile_contents(self, tmp_path: Path) -> None:
        """Lockfile should contain expected metadata."""
        _run(str(tmp_path))
        lockfile = tmp_path / ".specback_data" / "llockfile"
        lock = json.loads(lockfile.read_text(encoding="utf-8"))

        assert lock["specback_version"] == "1.2.0"
        assert "installed_at" in lock
        assert "hashes" in lock
        assert isinstance(lock["hashes"], dict)
        assert len(lock["hashes"]) > 0

        # Verify a specific hash（SKILL.md がスタンプされている）
        skill_md = ROOT / "skills" / "specback" / "SKILL.md"
        expected_hash = _sha256(skill_md)
        assert lock["hashes"].get(".claude/skills/specback/SKILL.md") == expected_hash

    def test_lockfile_no_pycache(self, tmp_path: Path) -> None:
        """Lockfile should not contain __pycache__ hashes."""
        _run(str(tmp_path))
        lockfile = tmp_path / ".specback_data" / "llockfile"
        lock = json.loads(lockfile.read_text(encoding="utf-8"))
        pycache_keys = [k for k in lock["hashes"] if "__pycache__" in k]
        assert len(pycache_keys) == 0, f"Found __pycache__ in lockfile: {pycache_keys}"

    def test_config_stamped(self, tmp_path: Path) -> None:
        """Config file should be a copy of the source config."""
        _run(str(tmp_path))
        # ADW廃止により sssf.config.yaml はコピーされない（Issue #236）
        target_config = tmp_path / ".specback_data" / "config" / "sssf.config.yaml"
        assert not target_config.exists()


class TestCheck:
    """--check (drift detection) tests."""

    def test_no_lockfile(self, tmp_path: Path) -> None:
        """Check on unstamped target should fail."""
        result = _run("--check", str(tmp_path))
        assert result.returncode == 1
        assert "No lockfile found" in result.stdout

    def test_no_drift(self, tmp_path: Path) -> None:
        """Fresh install should show no drift."""
        _run(str(tmp_path))
        result = _run("--check", str(tmp_path))
        assert result.returncode == 0
        assert "No drift detected" in result.stdout

    def test_drift_detected(self, tmp_path: Path) -> None:
        """Modified file should be detected as drift."""
        _run(str(tmp_path))
        # Modify a stamped file（SKILL.md を変更）
        skill_md = tmp_path / ".claude" / "skills" / "specback" / "SKILL.md"
        with open(skill_md, "a") as f:
            f.write("\n# DRIFT\n")
        result = _run("--check", str(tmp_path))
        assert result.returncode == 1
        assert "Modified files" in result.stdout
        assert ".claude/skills/specback/SKILL.md" in result.stdout

    def test_deleted_file_drift(self, tmp_path: Path) -> None:
        """Deleted stamped file should be detected."""
        _run(str(tmp_path))
        (tmp_path / ".claude" / "skills" / "specback" / "SKILL.md").unlink()
        result = _run("--check", str(tmp_path))
        assert result.returncode == 1
        assert "Removed files" in result.stdout

    def test_added_file_ignored(self, tmp_path: Path) -> None:
        """New files not tracked in lockfile should not cause errors."""
        _run(str(tmp_path))
        (tmp_path / "my_new_file.md").write_text("hello")
        result = _run("--check", str(tmp_path))
        assert result.returncode == 0
        assert "No drift detected" in result.stdout


class TestEdgeCases:
    """Edge cases."""

    def test_force_drift_reset(self, tmp_path: Path) -> None:
        """--force should reset drift state."""
        _run(str(tmp_path))
        # Create drift（SKILL.md を変更）
        skill_md = tmp_path / ".claude" / "skills" / "specback" / "SKILL.md"
        with open(skill_md, "a") as f:
            f.write("\n# DRIFT\n")
        # Force re-stamp
        result = _run("--force", str(tmp_path))
        assert result.returncode == 0
        # Check should now pass
        result = _run("--check", str(tmp_path))
        assert result.returncode == 0
        assert "No drift detected" in result.stdout
