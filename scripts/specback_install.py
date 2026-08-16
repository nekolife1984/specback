#!/usr/bin/env python3
"""specback install — SSSF-style skill stamp installer.

Stamps specback into a target project directory with lockfile-based drift
detection. Creates a clean separation between:

  - **Stamped** files (skill symlinks) — re-stampable on upgrade
  - **Project data** (.specback_data/) — user-customizable

Usage:
    specback install /path/to/target         # Stamp into target
    specback install --check /path            # Drift detection only
    specback install --force /path            # Overwrite stamped files
    specback install --dry-run /path          # Show what would be done
    specback install --help                   # This message

Options:
    --check       Drift detection (no changes made)
    --force       Overwrite stamped files (recommend git commit first)
    --dry-run     Show what would be stamped without writing
    --help        Show this message and exit
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from common import reject_nonfinite, sha256_file, utcnow_iso
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────

SPECBACK_DATA_DIR = ".specback_data"
CONFIG_DIR = "config"
TEMPLATES_DIR = "templates"
PROMPT_ENG_DIR = "prompt_engineering"
LOCKFILE_NAME = "llockfile"

# Dirs to stamp as-is into target root
# (ADWスクリプトは廃止済みのため空。Issue #236)
STAMP_DIRS: list[str] = []

# Dirs to copy as shared assets under the core skill path
SHARED_DIRS = ["scripts", "references", "schemas", "agents", "templates", "variants"]

SKILL_SRC = "skills/specback"
SEARCH_SKILL_SRC = "skills/specback-search"

AGENT_USER_PATHS: dict[str, str] = {
    "claude": ".claude/skills/specback",
    "codex": ".codex/skills/specback",
    "opencode": ".opencode/skills/specback",
    "copilot": ".github/skills/specback",
    "cursor": ".cursor/skills/specback",
    "other": ".agents/skills/specback",
}

# Files in stamped dirs that may be modified by the user (excluded from drift)
USER_MODIFIABLE_STAMPED_FILES: set[str] = set()


# ── Helpers ────────────────────────────────────────────────────────────────


def _find_project_root() -> Path:
    """Find the specback repo root (where pyproject.toml lives)."""
    # Walk up from the script location
    script_dir = Path(__file__).resolve().parent
    for candidate in [script_dir, script_dir.parent, Path.cwd()]:
        if (candidate / "pyproject.toml").exists() and (candidate / SKILL_SRC).is_dir():
            return candidate
    # Last resort: check CWD
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / SKILL_SRC).is_dir():
        return cwd
    print("Error: cannot find specback project root (pyproject.toml + skills/specback/)", file=sys.stderr)
    print("Run this script from within the specback repository.", file=sys.stderr)
    sys.exit(1)


def _sha256_dir_sorted(root: Path) -> dict[str, str]:
    """Recursively compute SHA-256 hashes for all files under *root*.

    Returns ``{relative_path: sha256_hex}`` sorted by relative path.
    """
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file() or fpath.name.startswith("."):
            continue
        # Exclude __pycache__ directories
        if "__pycache__" in fpath.parts:
            continue
        rel = fpath.relative_to(root)
        hashes[str(rel)] = sha256_file(fpath)
    return hashes


def _current_target_hashes(target: Path, stamp_dirs: list[str]) -> dict[str, str]:
    """Compute current SHA-256 hashes of stamped files *in the target*.

    Returns ``{relative_path: sha256_hex}`` for all files that were
    originally stamped into the target (.claude/skills/specback/,
    .claude/skills/specback-search/).
    """
    all_hashes: dict[str, str] = {}

    # 1. Stamped dirs
    for d in stamp_dirs:
        dir_path = target / d
        if dir_path.is_dir():
            hashes = _sha256_dir_sorted(dir_path)
            for rel, h in hashes.items():
                all_hashes[f"{d}/{rel}"] = h

    # 2. Core skill dir
    skill_dir = target / ".claude" / "skills" / "specback"
    if skill_dir.is_dir():
        hashes = _sha256_dir_sorted(skill_dir)
        for rel, h in hashes.items():
            all_hashes[f".claude/skills/specback/{rel}"] = h

    # 3. Search skill dir
    search_dir = target / ".claude" / "skills" / "specback-search"
    if search_dir.is_dir():
        hashes = _sha256_dir_sorted(search_dir)
        for rel, h in hashes.items():
            all_hashes[f".claude/skills/specback-search/{rel}"] = h

    # 4. Shared dirs under skill
    for d in SHARED_DIRS:
        dir_path = target / ".claude" / "skills" / "specback" / d
        if dir_path.is_dir():
            hashes = _sha256_dir_sorted(dir_path)
            for rel, h in hashes.items():
                all_hashes[f".claude/skills/specback/{d}/{rel}"] = h

    return all_hashes


def _load_lockfile(target: Path) -> dict[str, Any] | None:
    """Load lockfile from target. Returns None if missing or corrupt."""
    lockfile = target / SPECBACK_DATA_DIR / LOCKFILE_NAME
    if not lockfile.exists():
        return None
    try:
        return json.loads(
            lockfile.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def _write_lockfile(target: Path, hashes: dict[str, str], version: str) -> dict[str, Any]:
    """Write lockfile to target/.specback_data/llockfile."""
    lockfile_dir = target / SPECBACK_DATA_DIR
    lockfile_dir.mkdir(parents=True, exist_ok=True)

    lock_data: dict[str, Any] = {
        "installed_at": utcnow_iso(),
        "specback_version": version,
        "hashes": hashes,
        "user_modified": [],
    }
    (lockfile_dir / LOCKFILE_NAME).write_text(
        json.dumps(lock_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return lock_data


def _detect_drift(
    target: Path,
    stamp_dirs: list[str],
    existing_lock: dict[str, Any],
) -> dict[str, list[str]]:
    """Compare current file hashes in target against lockfile.

    Returns:
        ``{"modified": [...], "added": [...], "removed": [...], "ok": [...]}``
    """
    current = _current_target_hashes(target, stamp_dirs)
    locked = existing_lock.get("hashes", {})

    modified: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    ok: list[str] = []

    all_keys = set(locked.keys()) | set(current.keys())
    for key in sorted(all_keys):
        old = locked.get(key)
        new = current.get(key)
        if old is None:
            added.append(key)
        elif new is None:
            removed.append(key)
        elif old != new:
            modified.append(key)
        else:
            ok.append(key)

    return {
        "modified": modified,
        "added": added,
        "removed": removed,
        "ok": ok,
    }


def _source_stamp_hashes(project_root: Path) -> dict[str, str]:
    """Compute SHA-256 hashes of all files *in the specback repo* that would
    be stamped.

    Returns ``{relative_path: sha256_hex}`` keyed from what would be the
    target root.
    """
    all_hashes: dict[str, str] = {}

    # 1. Stamped dirs
    for d in STAMP_DIRS:
        src = project_root / d
        if src.is_dir():
            hashes = _sha256_dir_sorted(src)
            for rel, h in hashes.items():
                all_hashes[f"{d}/{rel}"] = h

    # 2. Skill dir
    skill_src = project_root / SKILL_SRC
    if skill_src.is_dir():
        hashes = _sha256_dir_sorted(skill_src)
        for rel, h in hashes.items():
            all_hashes[f".claude/skills/specback/{rel}"] = h

    # 3. Skill search
    search_src = project_root / SEARCH_SKILL_SRC
    if search_src.is_dir():
        hashes = _sha256_dir_sorted(search_src)
        for rel, h in hashes.items():
            all_hashes[f".claude/skills/specback-search/{rel}"] = h

    # 4. Shared dirs under skill path
    for d in SHARED_DIRS:
        src = project_root / d
        if src.is_dir():
            hashes = _sha256_dir_sorted(src)
            for rel, h in hashes.items():
                all_hashes[f".claude/skills/specback/{d}/{rel}"] = h

    return all_hashes


def _check_target(target: Path) -> None:
    """Validate that target exists and is a directory."""
    if not target.exists():
        print(f"Error: target not found: {target}", file=sys.stderr)
        sys.exit(1)
    if not target.is_dir():
        print(f"Error: target is not a directory: {target}", file=sys.stderr)
        sys.exit(1)


# ── Stamp operations ──────────────────────────────────────────────────────


def _ensure_specback_data(target: Path, project_root: Path, dry_run: bool) -> None:
    """Create .specback_data/ directory structure."""
    data_dir = target / SPECBACK_DATA_DIR
    if data_dir.exists() and not dry_run:
        return  # Already exists, don't overwrite user data

    if dry_run:
        print(f"  📁  {data_dir}/")
        print(f"  📁  {data_dir}/{CONFIG_DIR}/")
        print(f"  📁  {data_dir}/{TEMPLATES_DIR}/")
        print(f"  📁  {data_dir}/{PROMPT_ENG_DIR}/")
        return

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / CONFIG_DIR).mkdir(exist_ok=True)
    (data_dir / TEMPLATES_DIR).mkdir(exist_ok=True)
    (data_dir / PROMPT_ENG_DIR).mkdir(exist_ok=True)

    # Copy default config（ADW廃止のため sssf.config.yaml はコピーしない。Issue #236）

    print(f"  ✅ {data_dir}/")


def _stamp_dir(
    src: Path, dst: Path, force: bool, dry_run: bool, label: str = "",
) -> None:
    """Copy directory from src to dst.

    Args:
        src: Source directory within the specback repo.
        dst: Destination under target root.
        force: Overwrite existing files.
        dry_run: Only print actions.
        label: Human-readable label for output.
    """
    if not src.exists() or not src.is_dir():
        return

    if not force and dst.exists() and any(dst.iterdir()):
        # Check if any file differs
        src_hashes = _sha256_dir_sorted(src)
        dst_hashes = _sha256_dir_sorted(dst)
        if src_hashes == dst_hashes:
            if dry_run:
                print(f"  ♻️   {dst}/ {label}(unchanged)")
            else:
                print(f"  ♻️   {dst}/ (unchanged)")
            return

    if dry_run:
        print(f"  📦  {dst}/ {label}({len(list(src.rglob('*')))} items)")
        return

    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        s = str(item)
        d = str(dst / item.name)
        if item.is_dir():
            if dst.exists():
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    print(f"  ✅ {dst}/")


def _stamp_core_skill(
    project_root: Path, target: Path, force: bool, dry_run: bool,
) -> None:
    """Stamp core skill and shared assets under .claude/skills/specback/."""
    skill_base = target / ".claude" / "skills" / "specback"

    # Copy SKILL.md and phases/
    skill_src = project_root / SKILL_SRC
    if skill_src.is_dir():
        dst = skill_base
        _stamp_dir(skill_src, dst, force, dry_run, label="core skill")

    # Copy shared assets under the skill
    for d in SHARED_DIRS:
        src = project_root / d
        if src.is_dir():
            dst = skill_base / d
            _stamp_dir(src, dst, force, dry_run, label=f"shared/{d}")

    # Stamp specback-search skill
    search_src = project_root / SEARCH_SKILL_SRC
    if search_src.is_dir():
        dst = target / ".claude" / "skills" / "specback-search"
        _stamp_dir(search_src, dst, force, dry_run, label="search skill")


def _perform_stamp(
    project_root: Path,
    target: Path,
    force: bool,
    dry_run: bool,
) -> dict[str, str]:
    """Execute the full stamp operation. Returns the set of file hashes stamped."""
    print(f"\n  🏗️   Stamping specback into: {target}\n")

    # 1. Create .specback_data/
    _ensure_specback_data(target, project_root, dry_run)

    # 2. Stamp core skill + shared assets
    _stamp_core_skill(project_root, target, force, dry_run)

    # 4. Compute hashes for lockfile
    hashes = _source_stamp_hashes(project_root) if not dry_run else {}

    print()
    return hashes


# ── CLI modes ──────────────────────────────────────────────────────────────


def cmd_install(target: Path, force: bool, dry_run: bool, version: str) -> int:
    """Execute ``specback install``."""
    _check_target(target)
    project_root = _find_project_root()

    # Existing stamp check
    lockfile_path = target / SPECBACK_DATA_DIR / LOCKFILE_NAME
    existing_lock = _load_lockfile(target)
    if existing_lock and not force:
        print(f"  ⚠️   specback already installed in {target}")
        print(f"     Lockfile: {lockfile_path}")
        print("     Use --force to re-stamp (recommend git commit first)")
        print("     Use --check to detect drift")
        return 1

    hashes = _perform_stamp(project_root, target, force, dry_run)

    if not dry_run:
        _write_lockfile(target, hashes, version)
        print(f"  🔒  Lockfile written: {lockfile_path}")
        print(f"\n  ✅  specback v{version} stamped into {target}")
        print(f"     📁 {target / SPECBACK_DATA_DIR}/  → customize here")
        print(f"     📁 {target / '.claude' / 'skills' / 'specback'}/  → core skill")
    else:
        print("  🏁  Dry-run complete. No changes were made.\n")

    return 0


def cmd_check(target: Path) -> int:
    """Execute ``specback install --check``."""
    _check_target(target)

    existing_lock = _load_lockfile(target)
    if existing_lock is None:
        print(f"  ❌  No lockfile found at {target / SPECBACK_DATA_DIR / LOCKFILE_NAME}")
        print("     specback has not been stamped into this target.")
        return 1

    print(f"\n  🔍  Drift check for: {target}\n")
    print(f"     Installed: {existing_lock.get('installed_at', '?')}")
    print(f"     Version:   {existing_lock.get('specback_version', '?')}\n")

    drift = _detect_drift(
        target, STAMP_DIRS, existing_lock,
    )

    has_issues = False

    if drift["modified"]:
        has_issues = True
        print(f"  🔴  Modified files ({len(drift['modified'])}):")
        for f in drift["modified"]:
            print(f"       ~ {f}")
        print()

    if drift["added"]:
        has_issues = True
        print(f"  🟡  Added files ({len(drift['added'])}):")
        for f in drift["added"]:
            print(f"       + {f}")
        print()

    if drift["removed"]:
        has_issues = True
        print(f"  🟡  Removed files ({len(drift['removed'])}):")
        for f in drift["removed"]:
            print(f"       - {f}")
        print()

    if drift["ok"]:
        print(f"  ✅  Intact files: {len(drift['ok'])}")

    if not has_issues:
        print("  ✅  No drift detected. All stamped files match lockfile.")

    return 1 if has_issues else 0


# ── Argument parser ────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="specback install",
        description="Stamp specback into a target project directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  specback install /path/to/my-project\n"
            "  specback install --check /path/to/my-project\n"
            "  specback install --force /path/to/my-project\n"
            "  specback install --dry-run /path/to/my-project\n"
        ),
    )
    parser.add_argument(
        "target",
        type=str,
        help="Target project directory to stamp specback into",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        dest="check_mode",
        help="Drift detection (no changes made)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite stamped files (recommend git commit first)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would be stamped without writing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    target = Path(args.target).resolve()
    specback_version = "1.2.0"  # Keep in sync with pyproject.toml

    if args.check_mode:
        return cmd_check(target)
    else:
        return cmd_install(target, force=args.force, dry_run=args.dry_run, version=specback_version)


if __name__ == "__main__":
    sys.exit(main())
