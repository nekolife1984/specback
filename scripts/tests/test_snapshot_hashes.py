"""Smoke tests for snapshot-hashes.py (hash snapshot generator)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "snapshot-hashes.py"


def test_specback_dir_parses_as_path():
    """--specback-dir is parsed as a Path (common helper)."""
    import importlib.util
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("snapshot_hashes_core", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["snapshot_hashes_core"] = mod
    spec.loader.exec_module(mod)
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
