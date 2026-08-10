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
