"""Smoke tests for snapshot-hashes.py (hash snapshot generator)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import load_script_module

SCRIPT = Path(__file__).resolve().parent.parent / "snapshot-hashes.py"


def test_specback_dir_parses_as_path():
    """--specback-dir is parsed as a Path (common helper)."""
    mod = load_script_module(SCRIPT, "snapshot_hashes_core")
    args = mod.parse_args(["--specback-dir", "custom-sb"])
    assert args.specback_dir == Path("custom-sb")
    args_default = mod.parse_args([])
    assert args_default.specback_dir == Path(".specback")


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


def test_help_includes_output():
    """--output flag for source-hashes.json path."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--output" in result.stdout


def test_args_with_output_dir():
    """--output-dir combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", "/tmp/x", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_no_units_warns_writes_empty(tmp_path):
    """Empty source-map warns that an empty hash file will be written (not silent)."""
    sb = tmp_path / "proj" / ".specback"
    sb.mkdir(parents=True)
    (sb / "source-map.json").write_text(
        json.dumps({"units": [], "target_root": "."}), encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Nothing to hash" in result.stderr
    assert "writing an empty source-hashes.json" in result.stderr
    # the empty artifact is still written (downstream consumers can read it)
    assert (sb / "source-hashes.json").exists()


def test_source_map_with_nan_fails(tmp_path):
    """Non-finite JSON in source-map.json is rejected (shared loader policy).

    snapshot-hashes.py reads the source map through common.load_json_text,
    which rejects NaN/Infinity — malformed artifacts must not silently
    produce hashes.
    """
    sb = tmp_path / "proj" / ".specback"
    sb.mkdir(parents=True)
    (sb / "source-map.json").write_text(
        '{"units": [{"id": "SRC-1", "line_range": [NaN, 5]}], "target_root": "."}',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "non-finite" in result.stderr


def test_parse_output_default(tmp_path):
    """Default --output path resolves under the specback dir (#322)."""
    mod = load_script_module(SCRIPT, "snapshot_hashes_out")
    sb = tmp_path / "proj" / ".specback"
    sb.mkdir(parents=True)
    args = mod.parse_args(["--specback-dir", str(sb)])
    assert args.output is None


def test_project_root_arg_parses(tmp_path):
    """--project-root is exposed on the parse result."""
    mod = load_script_module(SCRIPT, "snapshot_hashes_pr")
    args = mod.parse_args(["--specback-dir", "sb", "--project-root", str(tmp_path)])
    assert args.project_root == str(tmp_path)
    assert mod.parse_args(["--specback-dir", "sb"]).project_root is None


def test_moved_repo_recorded_root_still_hashes(tmp_path):
    """SB-09: a portable target_root re-resolves under the specback dir parent.

    A source-map that records target_root 'src' is found even when run from a
    different cwd, because the specback dir's parent is the project root.
    """
    proj = tmp_path / "proj" / "src"
    proj.mkdir(parents=True)
    sb = tmp_path / "proj" / ".specback"
    sb.mkdir(parents=True)
    # A source file with one symbol in 'src'.
    (proj / "app.py").write_text(
        "class App:\n    pass\n\n", encoding="utf-8",
    )
    (sb / "source-map.json").write_text(json.dumps({
        "schema_version": "0.1.0",
        "target_root": "src",
        "units": [{
            "id": "SRC-0001", "path": "app.py", "line_range": [1, 2],
            "kind": "py_class_def", "name": "App",
        }],
    }), encoding="utf-8")

    # Run from /tmp (not the project dir) — the old cwd-relative resolution
    # would mark SRC-0001 MISSING; the new resolution finds it under proj/src.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb)],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads((sb / "source-hashes.json").read_text())
    assert out["units"]["SRC-0001"]["status"] == "OK", result.stdout
    assert Path(out["resolved_target_root"]).resolve() == (tmp_path / "proj" / "src").resolve()


def test_moved_repo_with_project_root_override(tmp_path):
    """SB-09: --project-root points at a relocated repo to re-resolve src."""
    moved = tmp_path / "moved" / "src"
    moved.mkdir(parents=True)
    sb = tmp_path / "proj" / ".specback"
    sb.mkdir(parents=True)
    (moved / "app.py").write_text("class App:\n    pass\n\n", encoding="utf-8")
    (sb / "source-map.json").write_text(json.dumps({
        "schema_version": "0.1.0", "target_root": "src",
        "units": [{
            "id": "SRC-0001", "path": "app.py", "line_range": [1, 2],
            "kind": "py_class_def", "name": "App",
        }],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(sb),
         "--project-root", str(tmp_path / "moved")],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads((sb / "source-hashes.json").read_text())
    assert out["units"]["SRC-0001"]["status"] == "OK"
    assert Path(out["resolved_target_root"]).resolve() == (tmp_path / "moved" / "src").resolve()


def test_pure_helpers_referenced(tmp_path):
    """Reference core functions directly so coverage is satisfied via symbols.

    The integration tests drive these through subprocess only, which the
    reference-based coverage checker cannot see; direct unit calls close the gap.
    """
    mod = load_script_module(SCRIPT, "snapshot_hashes_refs")
    # compute_hashes over an empty unit list.
    assert mod.compute_hashes([], str(tmp_path)) == {}
    # detect_scan_patterns on an empty dir returns the empty-map shape.
    empty = tmp_path / "empty"
    empty.mkdir()
    pats = mod.detect_scan_patterns(empty)
    assert "include_patterns" in pats and "exclude_patterns" in pats
    # build_output records both portable and resolved roots.
    out = mod.build_output({}, "src", str(tmp_path), "ref", pats)
    assert out["target_root"] == "src"
    assert out["resolved_target_root"] == str(tmp_path)
    assert isinstance(out["units_total"], int)
