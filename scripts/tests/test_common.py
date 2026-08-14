"""Unit tests for scripts/common.py (Issue #279 — shared micro-helpers).

Covers the foundation module added in chore/add-common-lib: ``utcnow_iso`` /
``load_json_text`` / ``atomic_write_json`` / ``sha256_file`` /
``add_specback_dir_arg``. The module only defines helpers; wiring onto
individual scripts happens in follow-up issues, so no script behaviour
changes are asserted here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add the scripts directory to sys.path so we can import common
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common


# ---------------------------------------------------------------------------
# utcnow_iso
# ---------------------------------------------------------------------------


def test_utcnow_iso_is_parseable_iso8601() -> None:
    parsed = datetime.fromisoformat(common.utcnow_iso())
    assert parsed.tzinfo is not None


def test_utcnow_iso_is_utc() -> None:
    parsed = datetime.fromisoformat(common.utcnow_iso())
    assert parsed.utcoffset() == timedelta(0)


def test_utcnow_iso_is_recent() -> None:
    now = datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(common.utcnow_iso())
    assert abs((parsed - now).total_seconds()) < 60


# ---------------------------------------------------------------------------
# load_json_text
# ---------------------------------------------------------------------------


def test_load_json_text_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"a": [1, 2, 3], "b": "x"}', encoding="utf-8")
    assert common.load_json_text(path) == {"a": [1, 2, 3], "b": "x"}


def test_load_json_text_accepts_str_path(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text("[]", encoding="utf-8")
    assert common.load_json_text(str(path)) == []


def test_load_json_text_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        common.load_json_text(tmp_path / "nope.json")


def test_load_json_text_missing_file_branches(tmp_path: Path) -> None:
    # Design note (#279): callers decide missing-file behaviour. The
    # FileNotFoundError subclass of OSError lets callers catch and fall back.
    def load_or_default(path: Path, fallback: object) -> object:
        try:
            return common.load_json_text(path)
        except FileNotFoundError:
            return fallback

    assert load_or_default(tmp_path / "nope.json", {"fallback": True}) == {
        "fallback": True
    }


def test_load_json_text_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        common.load_json_text(path)


def test_load_json_text_rejects_nonfinite(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"v": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        common.load_json_text(path)


def test_load_json_text_oversize_raises(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_text('"pad"', encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        common.load_json_text(path, max_bytes=2)


def test_load_json_text_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(OSError):
        common.load_json_text(link)


def test_load_json_text_rejects_fifo(tmp_path: Path) -> None:
    # A hostile repo could place a FIFO where a JSON artifact is expected;
    # the regular-file check must reject it instead of hanging.
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        common.load_json_text(fifo)


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_json_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    common.atomic_write_json(path, {"a": 1, "b": [1, 2]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2]}


def test_atomic_write_json_indent_two(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    common.atomic_write_json(path, {"a": 1})
    assert path.read_text(encoding="utf-8") == '{\n  "a": 1\n}\n'


def test_atomic_write_json_ensure_ascii_false(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    common.atomic_write_json(path, {"title": "仕様書"})
    assert "仕様書" in path.read_text(encoding="utf-8")


def test_atomic_write_json_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    common.atomic_write_json(path, {"v": 1})
    common.atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}


def test_atomic_write_json_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    common.atomic_write_json(path, {"v": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.json"]
    assert leftovers == []


def test_atomic_write_json_refuses_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text('{"keep": true}', encoding="utf-8")
    link = tmp_path / "out.json"
    link.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        common.atomic_write_json(link, {"evil": True})
    # The symlink target must be untouched.
    assert json.loads(victim.read_text(encoding="utf-8")) == {"keep": True}


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    payload = b"hello specback"
    path.write_bytes(payload)
    assert common.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert common.sha256_file(path) == hashlib.sha256(b"").hexdigest()


def test_sha256_file_chunked_large(tmp_path: Path) -> None:
    # > 64 KiB so the chunked read loop is exercised.
    payload = b"0123456789abcdef" * 28000  # 448 KiB
    path = tmp_path / "large.bin"
    path.write_bytes(payload)
    assert common.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_accepts_str_path(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x")
    assert common.sha256_file(str(path)) == hashlib.sha256(b"x").hexdigest()


# ---------------------------------------------------------------------------
# add_specback_dir_arg
# ---------------------------------------------------------------------------


def test_add_specback_dir_arg_adds_argument() -> None:
    parser = argparse.ArgumentParser()
    common.add_specback_dir_arg(parser)
    ns = parser.parse_args([])
    assert ns.specback_dir == Path(".specback")


def test_add_specback_dir_arg_custom_default() -> None:
    parser = argparse.ArgumentParser()
    common.add_specback_dir_arg(parser, default=".custom")
    assert parser.parse_args([]).specback_dir == Path(".custom")


def test_add_specback_dir_arg_parses_value() -> None:
    parser = argparse.ArgumentParser()
    common.add_specback_dir_arg(parser)
    ns = parser.parse_args(["--specback-dir", "/tmp/x"])
    assert ns.specback_dir == Path("/tmp/x")
    assert isinstance(ns.specback_dir, Path)
