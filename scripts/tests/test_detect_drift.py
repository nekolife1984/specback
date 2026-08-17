"""Smoke + security regression + core-logic tests for detect-drift.py (Phase 7)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from conftest import create_repo as _create_repo, load_script_module

SCRIPT = Path(__file__).resolve().parent.parent / "detect-drift.py"

# Import detect-drift.py as a module for pure-function tests (Issue #258),
# without leaking scripts/ onto sys.path (Issue #324).
drift = load_script_module(SCRIPT, "detect_drift_core")


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


def test_help_includes_mode():
    """--mode auto/git/hash appears in help."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout
    assert "auto" in result.stdout
    assert "git" in result.stdout
    assert "hash" in result.stdout


def test_help_includes_json():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--json" in result.stdout


def test_args_with_output_dir():
    """--output-dir combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", "/tmp/x", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_args_with_mode_hash():
    """--mode hash combined with --help doesn't error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "hash", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Security regression (Issue #253 — git base argument injection)
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit and a minimal .specback dir."""
    return _create_repo(tmp_path, with_spec=True)


def test_state_json_injection_rejected(tmp_path):
    """state.json generated_at_commit must not be passed to git as an option."""
    specback = _init_repo(tmp_path)
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": "--output=.specback/pwned.txt"}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback), "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid git ref" in result.stderr
    assert not (specback / "pwned.txt").exists()


def test_cli_base_option_injection_rejected(tmp_path):
    """--base='--output=...' must be rejected, not executed."""
    specback = _init_repo(tmp_path)
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--specback-dir", str(specback),
            "--mode", "git",
            "--base=--output=.specback/pwned.txt",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid git ref" in result.stderr
    assert not (specback / "pwned.txt").exists()


def test_state_json_valid_commit_ok(tmp_path):
    """A real commit hash in state.json still works."""
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback), "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "No changes detected" in result.stdout


def test_json_written_on_no_changes(tmp_path):
    """--json must produce drift-report.json even with zero changes (Issue #256).

    Gates consume drift-report.json; without this, the file only exists
    when a prior run recorded changes, making the gate depend on history.
    """
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "git", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"detect-drift failed:\n{result.stderr}"
    json_path = specback / "drift-report.json"
    assert json_path.exists(), "drift-report.json not written on no-changes path"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["changed_files"] == 0


def test_git_mode_detects_modified_file(tmp_path):
    """git mode CLI: a modified committed file surfaces in drift-report.md.

    Covers the changed-files path (M) end-to-end: real git diff output →
    parse_diff_name_status → analyze_impact → report.  The no-changes
    path was already pinned; this pins the change-detection path that was
    lost when the git helpers moved to git_utils.
    """
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    # Modify the committed file (uncommitted → git diff shows M)
    (tmp_path / "sample.py").write_text("x = 2\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"detect-drift failed:\n{result.stderr}"
    report = (specback / "drift-report.md").read_text(encoding="utf-8")
    assert "sample.py" in report
    assert "Modified" in report


def test_git_mode_detects_deleted_file(tmp_path):
    """git mode CLI: a deleted committed file surfaces as a D entry."""
    specback = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": commit}),
        encoding="utf-8",
    )
    (tmp_path / "sample.py").unlink()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "git"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"detect-drift failed:\n{result.stderr}"
    report = (specback / "drift-report.md").read_text(encoding="utf-8")
    assert "sample.py" in report
    assert "Deleted" in report


# ---------------------------------------------------------------------------
# Core logic unit tests (Issue #258)
# ---------------------------------------------------------------------------


def _source_map(units: list[dict], target_root: str = ".") -> dict:
    """Build a source_map dict in the shape load_source_map returns."""
    by_path: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    for u in units:
        by_path.setdefault(u["path"], []).append(u)
        by_id[u["id"]] = u
    return {"units": units, "by_path": by_path, "by_id": by_id,
            "stats": {}, "target_root": target_root}


def _trace(by_source: dict) -> dict:
    return {"by_source": by_source, "schema_version": "0.1.0"}


# --- hash_line_range ------------------------------------------------------


def test_hash_line_range_basic(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    digest, count = drift.hash_line_range(f, 1, 3)
    assert count == 3
    expected = hashlib.sha256(b"line1line2line3").hexdigest()
    assert digest == expected


def test_hash_line_range_partial_range(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    digest, count = drift.hash_line_range(f, 2, 3)
    assert count == 2
    expected = hashlib.sha256(b"bc").hexdigest()
    assert digest == expected


def test_hash_line_range_crlf_normalized(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("a\r\nb\r\n", encoding="utf-8")
    digest_crlf, count = drift.hash_line_range(f, 1, 2)
    assert count == 2
    expected = hashlib.sha256(b"ab").hexdigest()
    assert digest_crlf == expected


def test_hash_line_range_missing_file(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        drift.hash_line_range(tmp_path / "nope.py", 1, 5)


# --- compute_hash_changes -------------------------------------------------


def test_compute_hash_changes_detects_modify_delete_add(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "gone.py").write_text("y = 2\n", encoding="utf-8")

    # Snapshot: mod.py recorded as hash of "x = 1", gone.py as present,
    # plus one path NOT in source-map (new.py) to exercise the ADD path.
    mod_hash = hashlib.sha256(b"x = 1").hexdigest()
    source_hashes = {
        "units": {
            "SRC-0001": {"path": "src/mod.py", "line_range": [1, 1],
                         "hash": f"sha256:{mod_hash}", "line_count": 1,
                         "status": "OK"},
            "SRC-0002": {"path": "src/gone.py", "line_range": [1, 1],
                         "hash": f"sha256:{hashlib.sha256(b'y = 2').hexdigest()}",
                         "line_count": 1, "status": "OK"},
        }
    }
    # Modify mod.py, delete gone.py
    (src / "mod.py").write_text("x = 999\n", encoding="utf-8")
    (src / "gone.py").unlink()

    source_map = _source_map([
        {"id": "SRC-0001", "path": "src/mod.py"},
        {"id": "SRC-0002", "path": "src/gone.py"},
        {"id": "SRC-0003", "path": "src/new.py"},
    ], target_root=str(tmp_path))

    changes = drift.compute_hash_changes(source_hashes, source_map, str(tmp_path))
    by_file = {c["file"]: c["status"] for c in changes}
    assert by_file.get("src/mod.py") == "M"
    assert by_file.get("src/gone.py") == "D"
    assert by_file.get("src/new.py") == "A"


def test_compute_hash_changes_no_changes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "same.py").write_text("keep\n", encoding="utf-8")
    h = hashlib.sha256(b"keep").hexdigest()
    source_hashes = {
        "units": {
            "SRC-0001": {"path": "src/same.py", "line_range": [1, 1],
                         "hash": f"sha256:{h}", "line_count": 1, "status": "OK"},
        }
    }
    source_map = _source_map([{"id": "SRC-0001", "path": "src/same.py"}])
    changes = drift.compute_hash_changes(source_hashes, source_map, str(tmp_path))
    assert changes == []


def test_hash_mode_target_root_falls_back_to_source_map(tmp_path):
    """--mode hash with no target_root in source-hashes.json must not crash.

    Previously `source_hashes.get("target_root", source_map.get("target_root", "."))`
    returned None for an explicit null, and compute_hash_changes raised
    TypeError when joining against None. The or-chain fallback now
    resolves source_hashes -> source_map -> "." (pyrefly).
    """
    specback = tmp_path / ".specback"
    specback.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "same.py").write_text("keep\n", encoding="utf-8")
    h = hashlib.sha256(b"keep").hexdigest()
    # detect-drift always loads trace.json regardless of mode.
    (specback / "trace.json").write_text(
        json.dumps({"schema_version": "0.2.0", "by_section": {}}), encoding="utf-8")

    # source-hashes.json has no useful target_root (null), source-map has it.
    (specback / "source-hashes.json").write_text(
        json.dumps({
            "target_root": None,
            "units": {
                "SRC-0001": {"path": "src/same.py", "line_range": [1, 1],
                             "hash": f"sha256:{h}", "line_count": 1,
                             "status": "OK"},
            },
        }), encoding="utf-8")
    (specback / "source-map.json").write_text(
        json.dumps(_source_map(
            [{"id": "SRC-0001", "path": "src/same.py"}],
            target_root=str(tmp_path),
        )), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "hash", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "No changes detected" in result.stdout


def test_hash_mode_target_root_missing_uses_dot(tmp_path):
    """--mode hash with no target_root anywhere defaults to "." (pyrefly)."""
    specback = tmp_path / ".specback"
    specback.mkdir()
    # detect-drift always loads trace.json regardless of mode.
    (specback / "trace.json").write_text(
        json.dumps({"schema_version": "0.2.0", "by_section": {}}), encoding="utf-8")
    # Empty units: nothing to hash, but target_root resolution must not crash.
    (specback / "source-hashes.json").write_text(
        json.dumps({"units": {}}), encoding="utf-8")
    (specback / "source-map.json").write_text(
        json.dumps(_source_map([], target_root=None)), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--specback-dir", str(specback),
         "--mode", "hash", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# --- analyze_impact -------------------------------------------------------


def test_analyze_impact_modified_with_trace():
    changes = [{"status": "M", "file": "app/models/issue.rb"}]
    sm = _source_map([{"id": "SRC-0001", "path": "app/models/issue.rb"}])
    tr = _trace({
        "SRC-0001": {
            "path": "app/models/issue.rb",
            "covered_by_sections": [
                {"file": "01-overview.md", "section": "Overview"},
            ],
        }
    })
    result = drift.analyze_impact(changes, sm, tr)
    assert len(result["affected_sections"]) == 1
    entry = result["affected_sections"][0]
    assert entry["file"] == "app/models/issue.rb"
    assert entry["src_ids"] == ["SRC-0001"]
    assert entry["impacted_sections"][0]["impact"] == "moderate"
    assert result["section_keys_seen"] == ["01-overview.md::Overview"]


def test_analyze_impact_add_new_uncovered():
    changes = [{"status": "A", "file": "src/new.py"}]
    sm = _source_map([])  # new file not in source-map
    tr = _trace({})
    result = drift.analyze_impact(changes, sm, tr)
    assert len(result["new_uncovered"]) == 1
    assert result["new_uncovered"][0]["file"] == "src/new.py"
    assert result["affected_sections"] == []


def test_analyze_impact_delete_with_refs():
    changes = [{"status": "D", "file": "app/models/issue.rb"}]
    sm = _source_map([{"id": "SRC-0001", "path": "app/models/issue.rb"}])
    tr = _trace({
        "SRC-0001": {
            "path": "app/models/issue.rb",
            "covered_by_sections": [
                {"file": "01-overview.md", "section": "Overview"},
            ],
        }
    })
    result = drift.analyze_impact(changes, sm, tr)
    assert len(result["deleted_with_refs"]) == 1
    assert result["deleted_with_refs"][0]["status"] == "D"


def test_analyze_impact_no_impact():
    changes = [{"status": "M", "file": "app/not-mapped.rb"}]
    sm = _source_map([])
    tr = _trace({})
    result = drift.analyze_impact(changes, sm, tr)
    assert len(result["no_impact"]) == 1
    assert result["affected_sections"] == []
    assert result["new_uncovered"] == []


def test_analyze_impact_deleted_path_in_trace():
    """Deleted file NOT in source-map but referenced by path in trace."""
    changes = [{"status": "D", "file": "legacy.rb"}]
    sm = _source_map([])
    tr = _trace({
        "SRC-0099": {
            "path": "legacy.rb",
            "covered_by_sections": [
                {"file": "02-design.md", "section": "Legacy"},
            ],
        }
    })
    result = drift.analyze_impact(changes, sm, tr)
    assert len(result["deleted_with_refs"]) == 1
    assert result["deleted_with_refs"][0]["matched_by_path"] is True


# --- _determine_impact ----------------------------------------------------


def test_determine_impact_levels():
    assert drift._determine_impact("D", {}, {}) == drift.IMPACT_HIGH
    assert drift._determine_impact("A", {}, {}) == drift.IMPACT_HIGH
    assert drift._determine_impact("M", {}, {}) == drift.IMPACT_MODERATE
    assert drift._determine_impact("R", {}, {}) == drift.IMPACT_MODERATE
    assert drift._determine_impact("T", {}, {}) == drift.IMPACT_MODERATE
    assert drift._determine_impact("C", {}, {}) == drift.IMPACT_LOW


# --- loaders --------------------------------------------------------------


def test_load_source_map_builds_indexes(tmp_path):
    p = tmp_path / "source-map.json"
    p.write_text(json.dumps({
        "units": [
            {"id": "SRC-0001", "path": "a.py"},
            {"id": "SRC-0002", "path": "a.py"},
            {"id": "SRC-0003", "path": "b.py"},
        ],
    }), encoding="utf-8")
    sm = drift.load_source_map(p)
    assert sm["by_path"]["a.py"] == [
        {"id": "SRC-0001", "path": "a.py"},
        {"id": "SRC-0002", "path": "a.py"},
    ]
    assert sm["by_id"]["SRC-0003"]["path"] == "b.py"


def test_load_source_map_missing_exits(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        drift.load_source_map(tmp_path / "nope.json")


def test_load_state_returns_none_when_missing(tmp_path):
    assert drift.load_state(tmp_path / "state.json") is None


def test_load_state_parses(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"generated_at_commit": "abc123"}), encoding="utf-8")
    assert drift.load_state(p) == {"generated_at_commit": "abc123"}


def test_load_state_handles_bad_json(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not json", encoding="utf-8")
    assert drift.load_state(p) is None


# --- resolve_mode ----------------------------------------------------------


def test_resolve_mode_explicit(tmp_path):
    assert drift.resolve_mode("git", tmp_path) == "git"
    assert drift.resolve_mode("hash", tmp_path) == "hash"


def test_resolve_mode_auto_git(tmp_path):
    (tmp_path / ".git").mkdir()
    specback = tmp_path / ".specback"
    specback.mkdir()
    (specback / "state.json").write_text(
        json.dumps({"generated_at_commit": "abc"}), encoding="utf-8")
    assert drift.resolve_mode(None, specback) == "git"


def test_resolve_mode_auto_hash(tmp_path):
    specback = tmp_path / ".specback"
    specback.mkdir()
    (specback / "source-hashes.json").write_text("{}", encoding="utf-8")
    assert drift.resolve_mode(None, specback) == "hash"


def test_resolve_mode_auto_error(tmp_path):
    specback = tmp_path / ".specback"
    specback.mkdir()
    import pytest
    with pytest.raises(SystemExit):
        drift.resolve_mode(None, specback)


# --- generate_markdown / generate_json ------------------------------------


def test_generate_markdown_includes_summary():
    result = {
        "affected_sections": [], "new_uncovered": [],
        "deleted_with_refs": [], "no_impact": [],
        "section_keys_seen": [],
    }
    md = drift.generate_markdown(result, "HEAD", 0, ".specback")
    assert "# Drift Report" in md
    assert "## Summary" in md
    assert "No spec sections are affected" in md


def test_generate_markdown_with_affected():
    result = {
        "affected_sections": [{
            "file": "app/models/issue.rb", "status": "M",
            "src_ids": ["SRC-0001"],
            "impacted_sections": [
                {"file": "01-overview.md", "section": "Overview",
                 "impact": "moderate"},
            ],
        }],
        "new_uncovered": [], "deleted_with_refs": [], "no_impact": [],
        "section_keys_seen": ["01-overview.md::Overview"],
    }
    md = drift.generate_markdown(result, "main", 1, ".specback")
    assert "## Affected Spec Sections" in md
    assert "01-overview.md" in md
    assert "SRC-0001" in md


def test_generate_json_shape():
    result = {
        "affected_sections": [], "new_uncovered": [],
        "deleted_with_refs": [], "no_impact": [],
        "section_keys_seen": [],
    }
    data = drift.generate_json(result, "HEAD", 0)
    assert data["schema_version"] == drift.SCHEMA_VERSION
    assert data["summary"]["changed_files"] == 0
    assert data["summary"]["affected_spec_sections"] == 0


# --- remaining loaders / CLI helpers ---------------------------------------


def test_load_source_hashes(tmp_path):
    p = tmp_path / "source-hashes.json"
    p.write_text(json.dumps({"units": {"SRC-1": {"path": "a.py"}}}), encoding="utf-8")
    data = drift.load_source_hashes(p)
    assert data["units"]["SRC-1"]["path"] == "a.py"


def test_load_source_hashes_missing_exits(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        drift.load_source_hashes(tmp_path / "nope.json")


def test_load_trace_parses(tmp_path):
    p = tmp_path / "trace.json"
    p.write_text(json.dumps({"by_source": {}}), encoding="utf-8")
    assert drift.load_trace(p) == {"by_source": {}}


def test_load_trace_missing_exits(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        drift.load_trace(tmp_path / "nope.json")


def test_parse_args_defaults():
    args = drift.parse_args([])
    assert args.specback_dir == ".specback"
    assert args.mode is None
    assert args.base is None
    assert args.json is False


def test_parse_args_values():
    args = drift.parse_args([
        "--specback-dir", ".specback", "--mode", "git",
        "--base", "v1.0", "--json",
    ])
    assert args.mode == "git"
    assert args.base == "v1.0"
    assert args.json is True


def test_print_base_info_head(capsys):
    drift.print_base_info("HEAD")
    err = capsys.readouterr().err
    assert "HEAD" in err


def test_print_base_info_commit(capsys):
    drift.print_base_info("deadbeef12345678")
    err = capsys.readouterr().err
    assert "deadbeef1234" in err


def test_print_mode_info(capsys):
    drift.print_mode_info("git")
    assert "git" in capsys.readouterr().err


# --- loader delegation (Issue #283 — shared artifact_io.py) ---------------


def test_load_state_delegates_to_artifact_io(tmp_path):
    """drift.load_state returns artifact_io.load_state's value (identical impl)."""
    import artifact_io
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"generated_at_commit": "abc123"}), encoding="utf-8")
    assert drift.load_state(p) == artifact_io.load_state(p)
    assert drift.load_state(tmp_path / "missing.json") is None


def test_load_source_map_matches_artifact_io_indexes(tmp_path):
    """drift.load_source_map builds the same indexes as artifact_io."""
    import artifact_io
    p = tmp_path / "source-map.json"
    p.write_text(json.dumps({
        "units": [
            {"id": "SRC-0001", "path": "a.py"},
            {"id": "SRC-0002", "path": "a.py"},
            {"id": "SRC-0003", "path": "b.py"},
        ],
        "stats": {"files_scanned": 2},
        "target_root": "repo",
    }), encoding="utf-8")
    assert drift.load_source_map(p) == artifact_io.load_source_map(p, build_indexes=True)


def test_load_trace_matches_artifact_io(tmp_path):
    """drift.load_trace parses the same data as artifact_io.load_trace."""
    import artifact_io
    p = tmp_path / "trace.json"
    p.write_text(json.dumps({"by_source": {"SRC-0001": {"path": "a.py"}}}), encoding="utf-8")
    assert drift.load_trace(p) == artifact_io.load_trace(p)


def test_print_base_info_head_message(capsys):
    """print_base_info emits the HEAD fallback message exactly once to stderr (#322)."""
    drift.print_base_info("HEAD")
    captured = capsys.readouterr()
    assert "detect-drift.py: using --base HEAD" in captured.err
    assert "(no generated_at_commit in state.json)" in captured.err


def test_print_base_info_commit_message(capsys):
    """print_base_info shows the short base hash for a real ref (#322)."""
    drift.print_base_info("0123456789abcdef")
    captured = capsys.readouterr()
    assert "detect-drift.py: using --base 0123456789ab" in captured.err
    assert "from state.json.generated_at_commit" in captured.err
