"""Tests for adws/adw_specback_wbs.py — Phase 2 WBS ADW."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADW_WBS = ROOT / "adws" / "adw_specback_wbs.py"


def test_imports() -> None:
    result = subprocess.run([sys.executable, "-c", f"""
import sys; sys.path.insert(0, '{ROOT}')
from adws.adw_specback_wbs import run_wbs, build_parser, parse_chapters_from_template, generate_inventory, WBSOutput
print('  ✅ OK')
"""], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"


def test_help() -> None:
    result = subprocess.run([sys.executable, str(ADW_WBS), "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "specback ADW" in result.stdout


def test_no_target(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(ADW_WBS), "--target", str(tmp_path / "nonexistent")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "target directory not found" in result.stderr


def test_parse_chapters() -> None:
    from adws.adw_specback_wbs import parse_chapters_from_template
    tp = ROOT / "templates" / "web-app.md"
    if not tp.exists():
        pytest.skip("template not found")
    chapters = parse_chapters_from_template(tp)
    assert len(chapters) >= 3
    assert any(c["filename"] == "00-metadata.md" for c in chapters)


def test_parse_chapters_excludes_h2_section_names() -> None:
    """h2 のセクション名（Chapter outline / Customisation guidance）をチャプターとして抽出しない。"""
    from adws.adw_specback_wbs import parse_chapters_from_template
    tp = ROOT / "templates" / "web-app.md"
    if not tp.exists():
        pytest.skip("template not found")
    chapters = parse_chapters_from_template(tp)
    filenames = [c["filename"] for c in chapters]
    assert "chapter-outline.md" not in filenames
    assert "customisation-guidance.md" not in filenames


def test_parse_chapters_extracts_chapter_headings() -> None:
    """h3 の '### Chapter N: Title' をチャプターとして抽出する。"""
    from adws.adw_specback_wbs import parse_chapters_from_template
    tp = ROOT / "templates" / "web-app.md"
    if not tp.exists():
        pytest.skip("template not found")
    chapters = parse_chapters_from_template(tp)
    standard = [c for c in chapters if c["kind"] == "standard"]
    assert len(standard) >= 3
    assert any("Overview" in c["title"] for c in standard)
    assert any("Architecture" in c["title"] for c in standard)


def test_parse_chapters_all_templates() -> None:
    """全テンプレートで h3 チャプターが抽出され、h2 セクション名が混入しないこと。"""
    from adws.adw_specback_wbs import parse_chapters_from_template
    for tp in sorted((ROOT / "templates").glob("*.md")):
        chapters = parse_chapters_from_template(tp)
        standard = [c for c in chapters if c["kind"] == "standard"]
        assert len(standard) >= 3, f"{tp.name}: 標準チャプター不足 ({len(standard)})"
        filenames = [c["filename"] for c in chapters]
        assert "chapter-outline.md" not in filenames, f"{tp.name}: h2 セクションが混入"
        assert "customisation-guidance.md" not in filenames, f"{tp.name}: h2 セクションが混入"


def test_generate_inventory(tmp_path: Path) -> None:
    from adws.adw_specback_wbs import generate_inventory
    (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("print('x')", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    inv = generate_inventory(tmp_path)
    assert len(inv) >= 2
    types = {i["type"] for i in inv}
    assert "source" in types and "config" in types


def test_non_interactive(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('x')", encoding="utf-8")
    out = tmp_path / "specs"
    out.mkdir()
    result = subprocess.run([sys.executable, str(ADW_WBS), "--target", str(tmp_path), "--output-dir", str(out), "--non-interactive", "--envelope-out", str(tmp_path / "env.json")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"WBS failed:\n{result.stderr}"
    env = json.loads((tmp_path / "env.json").read_text())
    assert env["inventory_count"] >= 0
    assert len(env["chapters"]) >= 3
    assert (out / ".specback" / "wbs.json").exists()
