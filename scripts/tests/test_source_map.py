"""scripts/source-map.py — ソースマップ生成のテスト。

Issue #238: target の親ディレクトリ基準で相対パスが計算される問題の回帰テスト。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_MAP = ROOT / "scripts" / "source-map.py"


def _run_source_map(target: Path, output: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SOURCE_MAP), "--target", str(target),
         "--output", str(output), *extra],
        capture_output=True, text=True, timeout=60,
    )


def test_relative_paths_from_target_root(tmp_path: Path) -> None:
    """target 直下からの相対パスで path が生成される（Issue #238 回帰）。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    out = tmp_path / "out.json"

    result = _run_source_map(tmp_path, out)
    assert result.returncode == 0, f"source-map failed:\n{result.stderr}"

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [u["path"] for u in data.get("units", [])]
    # target 名（tmp_path.name）を含むプレフィックスが付かないこと
    assert "src/main.py" in paths, f"src/main.py が見つからない: {paths}"
    assert "app.py" in paths, f"app.py が見つからない: {paths}"
    assert not any(p.startswith(f"{tmp_path.name}/") for p in paths), (
        f"target 名プレフィックスが付いている: {paths}"
    )


def test_exclude_globs_apply(tmp_path: Path) -> None:
    """exclude-globs に指定したディレクトリが除外される。"""
    (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib" / "x.py").parent.mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "x.py").write_text("y = 1\n", encoding="utf-8")
    out = tmp_path / "out.json"

    result = _run_source_map(
        tmp_path, out,
        "--exclude-globs", "**/.venv/**,**/__pycache__/**",
    )
    assert result.returncode == 0, f"source-map failed:\n{result.stderr}"

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [u["path"] for u in data.get("units", [])]
    assert "src/main.py" in paths
    assert not any(".venv" in p for p in paths), f".venv が除外されていない: {paths}"


def test_help() -> None:
    """--help が動作する。"""
    result = subprocess.run(
        [sys.executable, str(SOURCE_MAP), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--target" in result.stdout


def test_matches_any_double_star_dir() -> None:
    """**/NAME/** 形式は任意階層の NAME セグメントにマッチする（Issue #238）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("source_map_mod", SOURCE_MAP)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["source_map_mod"] = mod
    spec.loader.exec_module(mod)
    matches_any = mod.matches_any

    # 相対パスの先頭が .venv でも src/.venv でもマッチする
    assert matches_any(".venv/lib/x.py", ["**/.venv/**"])
    assert matches_any("src/.venv/lib/x.py", ["**/.venv/**"])
    assert matches_any(".git/objects/x", ["**/.git/**"])
    assert matches_any("src/__pycache__/x.py", ["**/__pycache__/**"])
    # マッチしないケース
    assert not matches_any("src/app.py", ["**/.venv/**"])
    assert not matches_any("venv_backup/x.py", ["**/.venv/**"])


def test_venv_excluded_in_real_scan(tmp_path: Path) -> None:
    """.venv 配下が実際のスキャンから除外される（Issue #238 回帰）。"""
    (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "pkg" / "mod.py").parent.mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "pkg" / "mod.py").write_text(
        "def f():\n    pass\n", encoding="utf-8"
    )
    out = tmp_path / "out.json"

    result = _run_source_map(
        tmp_path, out,
        "--exclude-globs", "**/.venv/**,**/__pycache__/**,**/.git/**",
    )
    assert result.returncode == 0, f"source-map failed:\n{result.stderr}"

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [u["path"] for u in data.get("units", [])]
    assert "src/main.py" in paths
    assert not any(".venv" in p for p in paths), f".venv が除外されていない: {paths}"


def test_symlink_outside_target_ignored(tmp_path: Path) -> None:
    """target 外を指す symlink はスキャン対象外（Issue #254 回帰）。

    symlink を追跡して target 外のファイル内容が source-map.json に
    保存される脆弱性の再現テスト。シンボル名・クラス名が成果物に
    漏れないことを検証する。
    """
    import os

    secret = tmp_path.parent / "outside-secret.py"
    secret.write_text(
        "class SecretLeak:\n"
        "    def top_secret_func(self):\n"
        "        return 'hunter2'\n",
        encoding="utf-8",
    )
    try:
        (tmp_path / "secret-link.py").symlink_to(secret)
    except OSError:
        import pytest
        pytest.skip("symlink creation not supported on this platform")

    (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    out = tmp_path / "out.json"

    result = _run_source_map(tmp_path, out)
    assert result.returncode == 0, f"source-map failed:\n{result.stderr}"

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [u["path"] for u in data.get("units", [])]
    assert "src/main.py" in paths
    # symlink 経由の外部ファイル内容が source-map.json に漏れないこと
    assert "secret-link.py" not in paths, f"symlink がスキャンされた: {paths}"
    assert not any("SecretLeak" in str(u) for u in data.get("units", [])), (
        "target 外ファイルのシンボルが漏れている"
    )


def test_symlink_inside_target_ignored(tmp_path: Path) -> None:
    """target 内を指す symlink も追跡しない（通常ファイルとの二重登録防止）。"""
    import os

    (tmp_path / "real.py").write_text("def real():\n    pass\n", encoding="utf-8")
    try:
        (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")
    except OSError:
        import pytest
        pytest.skip("symlink creation not supported on this platform")

    out = tmp_path / "out.json"
    result = _run_source_map(tmp_path, out)
    assert result.returncode == 0, f"source-map failed:\n{result.stderr}"

    data = json.loads(out.read_text(encoding="utf-8"))
    paths = [u["path"] for u in data.get("units", [])]
    assert "real.py" in paths
    assert "alias.py" not in paths, f"target 内 symlink がスキャンされた: {paths}"


# ---------------------------------------------------------------------------
# Extractor unit tests (Issue #258 — table-driven)
# ---------------------------------------------------------------------------


def _load_source_map_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("source_map_ext", SOURCE_MAP)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["source_map_ext"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_extract_py_units_table() -> None:
    """extract_py_units: top-level class/def become units, methods do not."""
    mod = _load_source_map_module()
    ids = iter(["SRC-0001", "SRC-0002", "SRC-0003"])

    source = (
        "import os\n"
        "\n"
        "class User:\n"
        "    def name(self):\n"
        "        return 'x'\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def another():\n"
        "    pass\n"
    )
    units = list(mod.extract_py_units("app/models/user.py", source, lambda: next(ids)))
    kinds = [(u.kind, u.name) for u in units]
    assert ("py_class", "User") in kinds
    assert ("py_function", "helper") in kinds
    assert ("py_function", "another") in kinds
    # Methods inside a class are NOT separate units
    assert not any(u.name == "name" for u in units)
    # line ranges are 1-indexed and inclusive
    user = next(u for u in units if u.kind == "py_class")
    assert user.line_range == (3, 6)


def test_extract_ruby_units_table() -> None:
    """extract_ruby_units: class/def/module at top level become units."""
    mod = _load_source_map_module()
    ids = iter(["SRC-0001", "SRC-0002"])

    source = (
        "class Issue\n"
        "  def assignee\n"
        "    user\n"
        "  end\n"
        "end\n"
        "\n"
        "def top_helper\n"
        "  1\n"
        "end\n"
    )
    units = list(mod.extract_ruby_units("app/models/issue.rb", source, lambda: next(ids)))
    kinds = [(u.kind, u.name) for u in units]
    assert ("ruby_class", "Issue") in kinds
    assert ("ruby_function", "top_helper") in kinds
    # def inside class is part of the class block, not its own unit
    assert not any(u.name == "assignee" for u in units)
    issue = next(u for u in units if u.kind == "ruby_class")
    assert issue.line_range == (1, 5)


def test_extract_ruby_units_routes() -> None:
    """routes.rb: resources blocks are extracted as rails_route units."""
    mod = _load_source_map_module()
    ids = iter(["SRC-0001", "SRC-0002"])

    source = (
        "Rails.application.routes.draw do\n"
        "  resources :issues do\n"
        "    member do\n"
        "      get :close\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    units = list(mod.extract_ruby_units(
        "config/routes.rb", source, lambda: next(ids)))
    routes = [u for u in units if u.kind == "rails_route"]
    assert len(routes) >= 1
    assert any(u.name == "resources:issues" for u in routes)


def test_extract_js_units_table() -> None:
    """extract_js_units: export statements become units."""
    mod = _load_source_map_module()
    ids = iter(["SRC-0001", "SRC-0002"])

    source = (
        "export const foo = 1;\n"
        "const internal = 2;\n"
        "export function bar() {}\n"
    )
    units = list(mod.extract_js_units("src/index.js", source, lambda: next(ids)))
    names = sorted(u.name for u in units)
    assert names == ["bar", "foo"]
    assert all(u.kind == "js_export" for u in units)


def test_extract_file_unit() -> None:
    """extract_file_unit: whole-file coarse unit with first-line signature."""
    mod = _load_source_map_module()
    unit = mod.extract_file_unit(
        "db/migrate/001_init.rb",
        "class Init < ActiveRecord::Migration\n  def up\n  end\nend\n",
        "rails_migration",
        lambda: "SRC-0001",
    )
    assert unit.kind == "rails_migration"
    assert unit.name == "001_init.rb"
    assert unit.line_range == (1, 4)
    assert unit.signature.startswith("class Init")


# ---------------------------------------------------------------------------
# Remaining extractor helpers (Issue #258)
# ---------------------------------------------------------------------------


def test_fingerprint_deterministic() -> None:
    mod = _load_source_map_module()
    assert mod.fingerprint("hello") == mod.fingerprint("hello")
    assert mod.fingerprint("hello").startswith("sha1:")
    assert mod.fingerprint("hello") != mod.fingerprint("world")


def test_classify_file_strategies() -> None:
    mod = _load_source_map_module()
    assert "ruby_class_def" in mod.classify_file("app/models/user.rb")
    # rails_routes / rails_migration match paths WITH a leading slash segment
    assert "rails_routes" in mod.classify_file("app/config/routes.rb")
    assert "rails_migration" in mod.classify_file("app/db/migrate/001_init.rb")
    assert "py_class_def" in mod.classify_file("app/main.py")
    assert "js_export" in mod.classify_file("src/index.js")
    assert "js_export" in mod.classify_file("src/index.tsx")
    assert "view_file" in mod.classify_file("app/views/users/index.html.erb")
    assert "config_file" in mod.classify_file("config/settings.yml")
    assert "style_file" in mod.classify_file("app/assets/application.css")
    assert "sql_file" in mod.classify_file("db/schema.sql")
    # Markdown is skipped unless it's a README
    assert mod.classify_file("docs/guide.md") == []


def test_extract_py_block() -> None:
    mod = _load_source_map_module()
    lines = ["def f():", "    pass", "    return 1", "x = 2"]
    end = mod.extract_py_block(lines, 0, "")
    assert end == 3  # stops before x = 2 (index 3)


def test_extract_ruby_block() -> None:
    mod = _load_source_map_module()
    lines = ["class A", "  def b", "  end", "end", "x = 1"]
    end = mod.extract_ruby_block(lines, 0, "")
    assert end == 4  # 1-indexed line of the matching `end`


def test_source_unit_dataclass() -> None:
    mod = _load_source_map_module()
    u = mod.SourceUnit(
        id="SRC-1", path="a.py", line_range=(1, 2),
        kind="py_function", name="f", signature="def f():",
        fingerprint="sha1:abc",
    )
    assert u.id == "SRC-1"
    assert u.kind == "py_function"
    assert u.name == "f"


def test_iter_target_files_skips_dirs_and_globs(tmp_path: Path) -> None:
    mod = _load_source_map_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def m():\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "x.py").write_text("y = 1\n", encoding="utf-8")
    files = [p.name for p in mod.iter_target_files(tmp_path / "src", ["**/sub/**"])]
    assert files == ["main.py"]


def test_build_source_map_end_to_end(tmp_path: Path) -> None:
    """build_source_map returns units + stats over a tiny project."""
    mod = _load_source_map_module()
    (tmp_path / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    result = mod.build_source_map(tmp_path, ["**/.git/**"])
    assert result["stats"]["files_scanned"] >= 1
    paths = [u["path"] for u in result["units"]]
    assert "app.py" in paths
