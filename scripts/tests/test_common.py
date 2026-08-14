#!/usr/bin/env python3
"""Tests for common.py — shared micro-helpers for specback scripts."""

import sys
from pathlib import Path

# Ensure scripts/ (parent of tests/) is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (  # noqa: E402
    utcnow_iso,
    load_json_text,
    load_json_text_or,
    atomic_write_json,
    sha256_file,
    add_specback_dir_arg,
)

import json  # noqa: E402
import argparse  # noqa: E402
import pytest  # noqa: E402


class TestUtcnowIso:
    def test_returns_iso_string(self) -> None:
        s = utcnow_iso()
        from datetime import datetime
        parsed = datetime.fromisoformat(s)
        assert parsed.tzinfo is not None
        # Format check: matches datetime.now(timezone.utc).isoformat().
        assert s.endswith("+00:00")


class TestLoadJsonText:
    def test_loads_object(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        p.write_text('{"x": 1}', encoding="utf-8")
        assert load_json_text(p) == {"x": 1}

    def test_loads_non_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_json_text(p) == [1, 2, 3]

    def test_utf8_content(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        p.write_text('{"ja": "日本語"}', encoding="utf-8")
        assert load_json_text(p) == {"ja": "日本語"}

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_json_text(tmp_path / "nope.json")

    def test_malformed_raises_json_decode_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_json_text(p)

    def test_nonfinite_raises_value_error(self, tmp_path: Path) -> None:
        p = tmp_path / "nan.json"
        p.write_text('{"v": NaN}', encoding="utf-8")
        with pytest.raises(ValueError):
            load_json_text(p)


class TestLoadJsonTextOr:
    def test_returns_value_when_present(self, tmp_path: Path) -> None:
        p = tmp_path / "a.json"
        p.write_text('{"y": 2}', encoding="utf-8")
        assert load_json_text_or(p, {}) == {"y": 2}

    def test_returns_default_when_missing(self, tmp_path: Path) -> None:
        assert load_json_text_or(tmp_path / "nope.json", {}) == {}

    def test_returns_default_when_malformed(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{bad", encoding="utf-8")
        assert load_json_text_or(p, {"fallback": True}) == {"fallback": True}


class TestAtomicWriteJson:
    def test_writes_json(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        atomic_write_json(dest, {"a": 1, "b": [1, 2]})
        assert json.loads(dest.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2]}

    def test_writes_ensure_ascii_false(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        atomic_write_json(dest, {"ja": "日本語"})
        text = dest.read_text(encoding="utf-8")
        assert "日本語" in text

    def test_writes_indent_2(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        atomic_write_json(dest, {"a": 1, "b": 2})
        text = dest.read_text(encoding="utf-8")
        assert '"a": 1' in text  # value on the same line as its key
        assert text.startswith('{\n  "a": 1')

    def test_roundtrip(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        obj = {"msg": "こんにちは", "n": 3, "ok": True}
        atomic_write_json(dest, obj)
        assert load_json_text(dest) == obj

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        atomic_write_json(dest, {"a": 1})
        atomic_write_json(dest, {"b": 2})
        assert load_json_text(dest) == {"b": 2}

    def test_rejects_nonfinite(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        with pytest.raises(ValueError):
            atomic_write_json(dest, {"v": float("nan")})

    def test_renders_no_temp_leftover_on_write(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.json"
        atomic_write_json(dest, {"a": 1})
        leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
        assert leftovers == []


class TestSha256File:
    def test_known_digest(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_bytes(b"hello world")
        # sha256sum of "hello world"
        assert sha256_file(p) == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert sha256_file(p) == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_matches_hardlink_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.write_bytes(b"some content" * 1000)
        b = tmp_path / "b"
        b.write_bytes(b"some content" * 1000)
        assert sha256_file(a) == sha256_file(b)


class TestAddSpecbackDirArg:
    def test_default_specback(self) -> None:
        parser = argparse.ArgumentParser()
        add_specback_dir_arg(parser)
        args = parser.parse_args([])
        assert args.specback_dir == Path(".specback")

    def test_default_none(self) -> None:
        parser = argparse.ArgumentParser()
        add_specback_dir_arg(parser, default=None)
        args = parser.parse_args([])
        assert args.specback_dir is None

    def test_custom_default(self) -> None:
        parser = argparse.ArgumentParser()
        add_specback_dir_arg(parser, default="data/.specback")
        args = parser.parse_args([])
        assert args.specback_dir == Path("data/.specback")

    def test_explicit_value(self) -> None:
        parser = argparse.ArgumentParser()
        add_specback_dir_arg(parser)
        args = parser.parse_args(["--specback-dir", "/tmp/custom"])
        assert args.specback_dir == Path("/tmp/custom")
