#!/usr/bin/env python3
"""Tests for common.py — shared micro-helpers for specback scripts."""

import sys
from pathlib import Path

# Ensure scripts/ (parent of tests/) is importable.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (  # noqa: E402
    utcnow_iso,
    utcnow_iso_z,
    reject_nonfinite,
    sanitize_control,
    load_json_text,
    load_json_text_or,
    atomic_write_text,
    atomic_write_json,
    sha256_file,
    sha256_bytes,
    hash_line_range,
    add_specback_dir_arg,
)

import json  # noqa: E402
import argparse  # noqa: E402
import hashlib  # noqa: E402
import threading  # noqa: E402
import pytest  # noqa: E402


class TestUtcnowIso:
    def test_returns_iso_string(self) -> None:
        s = utcnow_iso()
        from datetime import datetime
        parsed = datetime.fromisoformat(s)
        assert parsed.tzinfo is not None
        # Format check: matches datetime.now(timezone.utc).isoformat().
        assert s.endswith("+00:00")


class TestUtcnowIsoZ:
    def test_returns_z_string(self) -> None:
        s = utcnow_iso_z()
        from datetime import datetime
        parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
        assert parsed.tzinfo is None  # Z denotes UTC
        assert s.endswith("Z")


class TestRejectNonfinite:
    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            reject_nonfinite("NaN")

    def test_rejects_infinity(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            reject_nonfinite("Infinity")


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

    def test_non_utf8_raises_unicode_decode_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad-utf8.json"
        p.write_bytes(b'{"v": "\xff\xfe"}')
        with pytest.raises(UnicodeDecodeError):
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

    def test_returns_default_when_nonfinite(self, tmp_path: Path) -> None:
        p = tmp_path / "nan.json"
        p.write_text('{"v": NaN}', encoding="utf-8")
        assert load_json_text_or(p, {"fallback": True}) == {"fallback": True}

    def test_returns_default_when_non_utf8(self, tmp_path: Path) -> None:
        p = tmp_path / "bad-utf8.json"
        p.write_bytes(b'{"v": "\xff\xfe"}')
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

    def test_no_temp_leftover_on_failure(self, tmp_path: Path) -> None:
        # The temp file must be cleaned up even when writing fails.
        dest = tmp_path / "out.json"
        with pytest.raises(ValueError):
            atomic_write_json(dest, {"v": float("nan")})
        leftovers = [p.name for p in tmp_path.iterdir()]
        assert leftovers == []

    def test_does_not_follow_symlink_tmp(self, tmp_path: Path) -> None:
        # A symlink planted where a temp file would land must never be
        # followed (mkstemp uses O_EXCL; matches repo O_NOFOLLOW policy).
        dest = tmp_path / "out.json"
        victim = tmp_path / "victim.txt"
        victim.write_text("do not clobber", encoding="utf-8")
        # Pre-plant a fixed-name symlink at the old tmp location.
        (tmp_path / "out.json.tmp").symlink_to(victim)
        atomic_write_json(dest, {"a": 1})
        assert victim.read_text(encoding="utf-8") == "do not clobber"
        assert load_json_text(dest) == {"a": 1}

    def test_concurrent_writes_do_not_fail(self, tmp_path: Path) -> None:
        dest = tmp_path / "shared.json"
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                for _ in range(10):
                    atomic_write_json(dest, {"i": i})
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert load_json_text(dest) in ({"i": i} for i in range(4))


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


class TestSha256Bytes:
    def test_known_digest(self) -> None:
        # sha256sum of "hello world"
        assert sha256_bytes(b"hello world") == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_empty_bytes(self) -> None:
        assert sha256_bytes(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_matches_sha256_file(self, tmp_path: Path) -> None:
        data = b"some content" * 1000
        p = tmp_path / "f"
        p.write_bytes(data)
        assert sha256_bytes(data) == sha256_file(p)


class TestHashLineRange:
    def test_hashes_full_file(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        digest, count = hash_line_range(p, 1, 100)
        assert count == 3
        # Must equal sha256 of the same content with newlines stripped.
        expected = hashlib.sha256(b"line1line2line3").hexdigest()
        assert digest == expected

    def test_hashes_subrange(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        digest, count = hash_line_range(p, 2, 3)
        assert count == 2
        expected = hashlib.sha256(b"line2line3").hexdigest()
        assert digest == expected

    def test_crlf_normalized(self, tmp_path: Path) -> None:
        lf = tmp_path / "lf.txt"
        crlf = tmp_path / "crlf.txt"
        lf.write_text("a\nb\n", encoding="utf-8")
        crlf.write_text("a\r\nb\r\n", encoding="utf-8")
        assert hash_line_range(lf, 1, 100) == hash_line_range(crlf, 1, 100)

    def test_bom_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "bom.txt"
        p.write_bytes(b"\xef\xbb\xbfhello\n")
        digest, count = hash_line_range(p, 1, 100)
        assert count == 1
        assert digest == hashlib.sha256(b"hello").hexdigest()

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            hash_line_range(tmp_path / "nope.txt", 1, 10)


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


class TestSanitizeControl:
    def test_replaces_esc_sequence(self) -> None:
        out = sanitize_control("a\x1b[2Jb")
        assert out == "a\\x1b[2Jb"
        assert "\x1b" not in out

    def test_replaces_bell_newline_tab(self) -> None:
        out = sanitize_control("x\x07y\nz\t")
        assert out == "x\\x07y\\x0az\\x09"

    def test_passes_through_plain_text(self) -> None:
        s = "plain /path/file.py §section"
        assert sanitize_control(s) == s


class TestLoadJsonTextOrDirectory:
    def test_returns_default_when_path_is_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "state.json"
        d.mkdir()
        assert load_json_text_or(d, "fallback") == "fallback"


class TestAtomicWriteText:
    def test_writes_text(self, tmp_path: Path) -> None:
        p = tmp_path / "out.md"
        atomic_write_text(p, "# hello\n")
        assert p.read_text(encoding="utf-8") == "# hello\n"

    def test_no_temp_leftover(self, tmp_path: Path) -> None:
        p = tmp_path / "out.md"
        atomic_write_text(p, "x")
        assert list(tmp_path.glob("out.md.*.tmp")) == []

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "out.md"
        p.write_text("old")
        atomic_write_text(p, "new")
        assert p.read_text(encoding="utf-8") == "new"

    def test_replaces_symlink_itself_not_target(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("secret")
        p = tmp_path / "out.md"
        p.symlink_to(target)
        atomic_write_text(p, "overwrite")
        # os.replace swaps the symlink entry, never following it.
        assert target.read_text(encoding="utf-8") == "secret"
        assert not p.is_symlink()
        assert p.read_text(encoding="utf-8") == "overwrite"

    def test_missing_parent_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "no" / "such" / "dir" / "out.md"
        with pytest.raises(FileNotFoundError):
            atomic_write_text(p, "x")
