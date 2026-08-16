"""CLI output-path guard tests for source_map_v2 (Issue #318).

Run from the scripts/ directory:
    python -m pytest source_map_v2/tests/test_cli_output_guard.py -q

Covers the --output symlink rejection and the atomic-write behaviour of
main() — the link target must never be overwritten, and a normal write
must produce a valid source-map JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from source_map_v2.__main__ import main


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    return root


def test_symlink_output_is_rejected_and_target_untouched(tmp_path, capsys):
    """Regression for #318: --output pointing at a symlink must not overwrite
    the link target, and must fail with exit code 2."""
    root = _make_project(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET DATA", encoding="utf-8")
    out = root / "map.json"
    try:
        out.symlink_to(victim)
    except OSError:
        pytest.skip("symlink not supported on this platform")

    code = main(["--target", str(root), "--output", str(out)])

    assert code == 2
    # The link target is untouched.
    assert victim.read_text(encoding="utf-8") == "SECRET DATA"
    # The symlink itself is still in place (not replaced).
    assert out.is_symlink()
    err = capsys.readouterr().err
    assert "cannot be a symlink" in err


def test_normal_output_writes_valid_source_map(tmp_path):
    """A plain (non-symlink) --output path is written atomically and is a
    valid source-map JSON document."""
    root = _make_project(tmp_path)
    out = root / "map.json"

    code = main(["--target", str(root), "--output", str(out)])

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "0.2.0"
    assert payload["stats"]["units_total"] >= 1


def test_output_parent_dirs_created(tmp_path):
    """--output with a non-existent parent directory is created (mkdir -p)."""
    root = _make_project(tmp_path)
    out = root / "deep" / "nested" / "map.json"

    code = main(["--target", str(root), "--output", str(out)])

    assert code == 0
    assert out.is_file()
