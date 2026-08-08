"""scripts/wbs_common.py — WBS 共通ロジックのテスト。

ADW 廃止（Issue #236）に伴い、scripts/tests/test_adw_wbs.py から移設した。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_imports() -> None:
    """Verify the wbs_common module imports without errors."""
    result = subprocess.run(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{ROOT}')
from scripts.wbs_common import (
    parse_chapters_from_template, generate_inventory,
    load_gitignore_patterns, is_ignored,
)
print('  ✅ wbs_common imports OK')
"""],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"


def test_parse_chapters() -> None:
    from scripts.wbs_common import parse_chapters_from_template
    tp = ROOT / "templates" / "web-app.md"
    if not tp.exists():
        pytest.skip("template not found")
    chapters = parse_chapters_from_template(tp)
    assert len(chapters) >= 3
    assert any(c["filename"] == "00-metadata.md" for c in chapters)


def test_parse_chapters_excludes_h2_section_names() -> None:
    """h2 のセクション名（Chapter outline / Customisation guidance）をチャプターとして抽出しない。"""
    from scripts.wbs_common import parse_chapters_from_template
    tp = ROOT / "templates" / "web-app.md"
    if not tp.exists():
        pytest.skip("template not found")
    chapters = parse_chapters_from_template(tp)
    filenames = [c["filename"] for c in chapters]
    assert "chapter-outline.md" not in filenames
    assert "customisation-guidance.md" not in filenames


def test_parse_chapters_extracts_chapter_headings() -> None:
    """h3 の '### Chapter N: Title' をチャプターとして抽出する。"""
    from scripts.wbs_common import parse_chapters_from_template
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
    from scripts.wbs_common import parse_chapters_from_template
    for tp in sorted((ROOT / "templates").glob("*.md")):
        chapters = parse_chapters_from_template(tp)
        standard = [c for c in chapters if c["kind"] == "standard"]
        assert len(standard) >= 3, f"{tp.name}: 標準チャプター不足 ({len(standard)})"
        filenames = [c["filename"] for c in chapters]
        assert "chapter-outline.md" not in filenames, f"{tp.name}: h2 セクションが混入"
        assert "customisation-guidance.md" not in filenames, f"{tp.name}: h2 セクションが混入"


def test_generate_inventory(tmp_path: Path) -> None:
    from scripts.wbs_common import generate_inventory
    (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("print('x')", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    inv = generate_inventory(tmp_path)
    assert len(inv) >= 2
    types = {i["type"] for i in inv}
    assert "source" in types and "config" in types


def test_generate_inventory_excludes_venv(tmp_path: Path) -> None:
    """.venv 配下のライブラリを inventory に含めない。"""
    from scripts.wbs_common import generate_inventory
    (tmp_path / "src" / "app.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "PIL").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "PIL" / "Image.py").write_text(
        "class Image: pass\n", encoding="utf-8"
    )
    inv = generate_inventory(tmp_path)
    files = [i["file"] for i in inv]
    assert any(f.endswith("src/app.py") for f in files)
    assert not any(".venv" in f for f in files), f".venv が含まれてしまった: {files}"


def test_generate_inventory_excludes_git_and_caches(tmp_path: Path) -> None:
    """.git / __pycache__ / .pytest_cache を inventory に含めない。"""
    from scripts.wbs_common import generate_inventory
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ab").write_text("binary", encoding="utf-8")
    (tmp_path / "__pycache__" / "app.cpython-314.pyc").parent.mkdir(parents=True)
    (tmp_path / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\x00")
    (tmp_path / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
    (tmp_path / ".pytest_cache" / "v" / "cache" / "lastfailed").write_text("{}", encoding="utf-8")
    inv = generate_inventory(tmp_path)
    files = [i["file"] for i in inv]
    assert "app.py" in files
    assert not any(".git" in f for f in files)
    assert not any("__pycache__" in f for f in files)
    assert not any(".pytest_cache" in f for f in files)


def test_generate_inventory_respects_gitignore(tmp_path: Path) -> None:
    """.gitignore に書かれたカスタムパターン（data/）が inventory から除外される。"""
    from scripts.wbs_common import generate_inventory
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "data" / "raw.csv").parent.mkdir(parents=True)
    (tmp_path / "data" / "raw.csv").write_text("a,b\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("data/\n", encoding="utf-8")
    inv = generate_inventory(tmp_path)
    files = [i["file"] for i in inv]
    assert "app.py" in files
    assert not any("data" in f for f in files), f"data/ が除外されていない: {files}"


def test_generate_inventory_gitignore_negation(tmp_path: Path) -> None:
    """.gitignore の否定パターン（!important/）で再includeされる。"""
    from scripts.wbs_common import generate_inventory
    (tmp_path / "data" / "skip.py").parent.mkdir(parents=True)
    (tmp_path / "data" / "skip.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "data" / "important" / "keep.py").parent.mkdir(parents=True)
    (tmp_path / "data" / "important" / "keep.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "data/\n!data/important/\n", encoding="utf-8"
    )
    inv = generate_inventory(tmp_path)
    files = [i["file"] for i in inv]
    assert not any("skip.py" in f for f in files)
    assert any("important/keep.py" in f for f in files), f"否定パターンが機能していない: {files}"


def test_ignore_patterns_no_gitignore(tmp_path: Path) -> None:
    """.gitignore が無い場合、全ファイルが inventory に含まれる。"""
    from scripts.wbs_common import generate_inventory
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# doc\n", encoding="utf-8")
    inv = generate_inventory(tmp_path)
    files = [i["file"] for i in inv]
    assert "a.py" in files and "b.md" in files


def test_gitignore_pattern_to_regex() -> None:
    """gitignore パターン→正規表現変換の主要ケース。"""
    from scripts.wbs_common import _gitignore_pattern_to_regex
    import re
    cases = [
        # (パターン, マッチするパス, マッチしないパス)
        ("data/", "data/raw.csv", "src/data_util.py"),
        ("*.log", "error.log", "src/error_logger.py"),
        ("**/temp/*.py", "a/temp/x.py", "temp.py"),
        ("/root_only.py", "root_only.py", "src/root_only.py"),
        ("build", "build/out.py", "rebuild.py"),
    ]
    for pattern, should_match, should_not in cases:
        regex = _gitignore_pattern_to_regex(pattern)
        assert re.search(regex, should_match), f"{pattern!r} が {should_match!r} にマッチすべき"
        assert not re.search(regex, should_not), f"{pattern!r} が {should_not!r} にマッチすべきでない"
