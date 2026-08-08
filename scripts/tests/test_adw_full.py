"""adws/adw_specback_full.py — Full pipeline ADW の引数組み立てロジックのテスト。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _make_args(**kwargs: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "target": "/tmp/codebase",
        "output_dir": "/tmp/specs",
        "adw_id": None,
        "specback_dir": None,
        "non_interactive": False,
        "skip_phases": None,
        "from_phase": None,
        "depth_mode": "outline",
        "language": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _import_full() -> types.ModuleType:
    sys.path.insert(0, str(ROOT))
    import adws.adw_specback_full as full

    return full


def test_imports() -> None:
    """Verify the full pipeline module imports without errors."""
    result = subprocess.run(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{ROOT}')
from adws.adw_specback_full import (
    PHASES, _build_phase_args, build_parser,
)
print('  ✅ ADW full imports OK')
"""],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"


def test_all_phases_have_args_key() -> None:
    """全フェーズが "args" キーを持つこと。"""
    full = _import_full()
    for phase in full.PHASES:
        assert "args" in phase, f"{phase['name']} に args キーがない"


def test_recon_does_not_receive_output_dir() -> None:
    """recon フェーズは --output-dir を受け取らない（受け付けないため）。"""
    full = _import_full()
    recon = next(p for p in full.PHASES if p["name"] == "recon")
    args = _make_args()
    phase_args = full._build_phase_args(recon, ["--target", "/tmp/codebase"], args)
    assert "--output-dir" not in phase_args
    assert "--target" in phase_args


def test_investigate_receives_depth_mode() -> None:
    """investigate フェーズは --depth-mode を受け取る。"""
    full = _import_full()
    investigate = next(p for p in full.PHASES if p["name"] == "investigate")
    args = _make_args(depth_mode="comprehensive")
    phase_args = full._build_phase_args(investigate, ["--target", "/tmp/codebase"], args)
    assert "--depth-mode" in phase_args
    assert phase_args[phase_args.index("--depth-mode") + 1] == "comprehensive"
    assert "--output-dir" in phase_args


def test_setup_receives_language() -> None:
    """setup フェーズは --language を受け取る。"""
    full = _import_full()
    setup = next(p for p in full.PHASES if p["name"] == "setup")
    args = _make_args(language="ja")
    phase_args = full._build_phase_args(setup, ["--target", "/tmp/codebase"], args)
    assert "--language" in phase_args
    assert phase_args[phase_args.index("--language") + 1] == "ja"


def test_other_phases_do_not_receive_language_or_depth_mode() -> None:
    """recon/wbs/verify/refine/deliver/drift/changespec は --language / --depth-mode を受け取らない。"""
    full = _import_full()
    args = _make_args(depth_mode="outline", language="ja")
    for phase in full.PHASES:
        if phase["name"] in ("setup", "investigate"):
            continue
        phase_args = full._build_phase_args(phase, ["--target", "/tmp/codebase"], args)
        assert "--language" not in phase_args, f"{phase['name']} が --language を受け取ってしまった"
        assert "--depth-mode" not in phase_args, f"{phase['name']} が --depth-mode を受け取ってしまった"


def test_output_dir_defaults_to_specs() -> None:
    """--output-dir 未指定時は specs ディレクトリに解決される。"""
    full = _import_full()
    investigate = next(p for p in full.PHASES if p["name"] == "investigate")
    args = _make_args(output_dir=None)
    phase_args = full._build_phase_args(investigate, ["--target", "/tmp/codebase"], args)
    assert "--output-dir" in phase_args
    resolved = phase_args[phase_args.index("--output-dir") + 1]
    assert resolved.endswith("specs")


def test_non_interactive_passed_only_to_supported_phases() -> None:
    """--non-interactive は対応フェーズ（setup/recon/wbs/refine/changespec）のみに渡る。"""
    full = _import_full()
    args = _make_args(non_interactive=True)
    supported = {"setup", "recon", "wbs", "refine", "changespec"}
    for phase in full.PHASES:
        phase_args = full._build_phase_args(phase, ["--target", "/tmp/codebase"], args)
        if phase["name"] in supported:
            assert "--non-interactive" in phase_args, (
                f"{phase['name']} に --non-interactive が渡っていない"
            )
        else:
            assert "--non-interactive" not in phase_args, (
                f"{phase['name']} に --non-interactive が渡ってしまった"
            )
