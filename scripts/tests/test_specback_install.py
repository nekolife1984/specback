"""Tests for scripts/specback_install.py — Stamp installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = ROOT / "scripts" / "specback_install.py"

# Load the script as a module for unit tests (imports common).
sys.path.insert(0, str(INSTALL_SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("specback_install_core", INSTALL_SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["specback_install_core"] = mod  # register before exec_module
_spec.loader.exec_module(mod)


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


def test_sha256_uses_common_helper(tmp_path: Path) -> None:
    """sha256 hashing delegates to common.sha256_file (single home)."""
    from common import sha256_file
    f = tmp_path / "sample.txt"
    f.write_text("hello specback", encoding="utf-8")
    assert mod._sha256_dir_sorted(tmp_path) == {"sample.txt": sha256_file(f)}


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


class TestDevExclusion:
    """Dev-only artifacts must not ship into the target (pytest, caches,
    dev requirements) — mirrored across the stamp installer, install.sh
    and install.ps1."""

    def test_is_dev_excluded_dirs(self) -> None:
        """Known dev-only dir names are excluded by _is_dev_excluded."""
        assert mod._is_dev_excluded(("scripts", "tests"))
        assert mod._is_dev_excluded(("scripts", "source_map_v2", "tests"))
        assert mod._is_dev_excluded(("scripts", "__pycache__"))
        assert mod._is_dev_excluded(("scripts", ".pytest_cache"))
        assert mod._is_dev_excluded(("scripts", ".specback"))
        assert mod._is_dev_excluded(("scripts", "graphify-out"))

    def test_is_dev_excluded_hidden_and_files(self) -> None:
        """Hidden entries and dev requirements files are excluded."""
        assert mod._is_dev_excluded(("scripts", "dev-requirements.txt"))
        assert mod._is_dev_excluded(("scripts", ".hidden-dir", "x.py"))
        assert mod._is_dev_excluded((".env"))

    def test_is_dev_excluded_not_runtime(self) -> None:
        """Runtime scripts must NOT be excluded."""
        assert not mod._is_dev_excluded(("scripts", "common.py"))
        assert not mod._is_dev_excluded(("scripts", "requirements.txt"))
        assert not mod._is_dev_excluded(("scripts", "source_map_v2", "model.py"))
        assert not mod._is_dev_excluded(("SKILL.md",))
        # Empty / single non-dev path is fine
        assert not mod._is_dev_excluded(())

    def test_stamp_skips_tests(self, tmp_path: Path) -> None:
        """A real stamp must not copy tests/, __pycache__/, caches, or
        dev-requirements.txt into the target; runtime assets survive."""
        _run(str(tmp_path))
        scripts_dest = tmp_path / ".claude" / "skills" / "specback" / "scripts"
        assert (scripts_dest / "common.py").exists()
        assert (scripts_dest / "requirements.txt").exists()
        assert (scripts_dest / "source_map_v2" / "model.py").exists()
        # Dev artifacts absent across shared scripts + search skill
        assert not (scripts_dest / "tests").exists()
        assert not (scripts_dest / "source_map_v2" / "tests").exists()
        assert not (scripts_dest / "dev-requirements.txt").exists()
        search_scripts = tmp_path / ".claude" / "skills" / "specback-search" / "scripts"
        assert not (search_scripts / "tests").exists()
        assert not (search_scripts / "__pycache__").exists()

    def test_lockfile_excludes_dev(self, tmp_path: Path) -> None:
        """Lockfile hashes must not reference dev-only artifacts.

        Keys are target-root-relative (e.g. ``.claude/.../scripts/tests/..``)
        so we check only for the dev artifact names, not a generic dot prefix
        (``.claude`` and ``.specback_data`` are legitimate).
        """
        _run(str(tmp_path))
        lock = json.loads(
            (tmp_path / ".specback_data" / "llockfile").read_text(encoding="utf-8")
        )
        dev_names = (
            "tests", "__pycache__", ".pytest_cache", ".mypy_cache",
            ".ruff_cache", ".specback", "graphify-out", "dev-requirements.txt",
        )
        bad = [
            k for k in lock["hashes"]
            if any(part in dev_names for part in Path(k).parts)
        ]
        assert bad == [], f"Dev artifacts leaked into lockfile: {bad}"


class TestCheck:
    """--check (drift detection) tests."""

    def test_no_lockfile(self, tmp_path: Path) -> None:
        """Check on unstamped target should fail."""
        result = _run("--check", str(tmp_path))
        assert result.returncode == 1
        assert "No lockfile found" in result.stdout

    def test_load_lockfile_rejects_nonfinite(self, tmp_path: Path) -> None:
        """A lockfile containing NaN must yield None (reject_nonfinite,
        Issue #314) instead of propagating a ValueError."""
        sb_data = tmp_path / ".specback_data"
        sb_data.mkdir()
        lockfile = sb_data / "llockfile"
        lockfile.write_text(
            '{"specback_version": "1.2.0", "bad": NaN}', encoding="utf-8"
        )
        assert mod._load_lockfile(tmp_path) is None

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


def test_parse_args_returns_namespace() -> None:
    """parse_args(argv) returns a Namespace (Issue #286)."""
    ns = mod.parse_args(["--check", "/tmp/nonexistent-target"])
    assert ns.check_mode is True
    assert ns.dry_run is False
    assert ns.target == "/tmp/nonexistent-target"


def test_main_accepts_argv(tmp_path: Path) -> None:
    """main(argv) returns an int exit code (Issue #286)."""
    rc = mod.main(["--check", str(tmp_path)])
    assert isinstance(rc, int)
