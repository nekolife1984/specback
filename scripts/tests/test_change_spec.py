#!/usr/bin/env python3
"""
Tests for change-spec.py (Phase 7c).

Tests core analysis functions against controlled fixtures
without touching the real filesystem or git.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add the scripts directory to sys.path so we can import
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import importlib.util
_cs_path = str(SCRIPTS / "change-spec.py")
_spec = importlib.util.spec_from_file_location("change_spec", _cs_path)
assert _spec is not None, f"Could not load spec from {_cs_path}"
_cs = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None, f"Loader not found for {_cs_path}"
_spec.loader.exec_module(_cs)

# Re-export
MODE_AUTO = _cs.MODE_AUTO
MODE_GIT = _cs.MODE_GIT
MODE_HASH = _cs.MODE_HASH
analyze_structural_changes = _cs.analyze_structural_changes
build_change_spec = _cs.build_change_spec
compute_hash_changes = _cs.compute_hash_changes
cross_reference_sections = _cs.cross_reference_sections
extract_snippets = _cs.extract_snippets
load_source_map = _cs.load_source_map
load_state = _cs.load_state
load_trace = _cs.load_trace
parse_unified_diff = _cs.parse_unified_diff
resolve_base = _cs.resolve_base
resolve_mode = _cs.resolve_mode
_parse_name_status_from_unified = _cs._parse_name_status_from_unified
_detect_symbols = _cs._detect_symbols


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNIFIED_DIFF_RUBY = r"""diff --git a/app/models/issue.rb b/app/models/issue.rb
index abc123..def456 100644
--- a/app/models/issue.rb
+++ b/app/models/issue.rb
@@ -1,5 +1,8 @@
 class Issue
-  def assignee
-    team_members.find { |m| m.role == "assignee" } || default_assignee
-  end
+
+  def assignee
+    assigned_member || default_assignee
+  end
+
+  def assigned_member
+    team_members.find { |m| m.role == "assignee" }
+  end
 end
"""

UNIFIED_DIFF_NEW_FILE = r"""diff --git a/app/services/bulk_export.rb b/app/services/bulk_export.rb
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/app/services/bulk_export.rb
@@ -0,0 +1,20 @@
+class BulkExport
+  def self.call(filter:, format: :csv)
+    # implementation
+  end
+
+  def format
+    @format || :csv
+  end
+end
"""

UNIFIED_DIFF_DELETED_FILE = r"""diff --git a/app/old/legacy_import.rb b/app/old/legacy_import.rb
deleted file mode 100644
index abc123..0000000
--- a/app/old/legacy_import.rb
+++ /dev/null
@@ -1,10 +0,0 @@
-class LegacyImport
-  def self.call
-    # old implementation
-  end
-end
"""

UNIFIED_DIFF_PYTHON = r"""diff --git a/src/processor.py b/src/processor.py
index 111222..333444 100644
--- a/src/processor.py
+++ b/src/processor.py
@@ -1,8 +1,12 @@
 import os
+import json
 from typing import Optional
-from typing import List
+from typing import Sequence

 def process(items):
-    return [transform(i) for i in items]
+    return [transform(i, validate=True) for i in items]
+
+
+def validate_item(item):
+    return item is not None
"""

UNIFIED_DIFF_GO = r"""diff --git a/cmd/server/main.go b/cmd/server/main.go
index 555666..777888 100644
--- a/cmd/server/main.go
+++ b/cmd/server/main.go
@@ -1,7 +1,9 @@
 package main

+import "net/http"
+
 func main() {
-    fmt.Println("starting")
+    http.ListenAndServe(":8080", nil)
 }

 func setup() {
 }

+func health() bool {
+    return true
+}
"""

SAMPLE_SOURCE_MAP = {
    "schema_version": "0.2.0",
    "units": [
        {
            "id": "SRC-0142",
            "path": "app/models/issue.rb",
            "kind": "ruby_class",
            "line_range": [1, 15],
            "name": "Issue",
        },
        {
            "id": "SRC-0143",
            "path": "app/services/bulk_export.rb",
            "kind": "ruby_class",
            "line_range": [1, 20],
            "name": "BulkExport",
        },
    ],
    "stats": {"total_units": 2},
    "target_root": "/fake/project",
}

SAMPLE_TRACE = {
    "schema_version": "0.1.0",
    "by_source": {
        "SRC-0142": {
            "path": "app/models/issue.rb",
            "line_range": [1, 15],
            "covered_by_sections": [
                {"file": "02-entities.md", "section": "2.1 Issue"},
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseUnifiedDiff(unittest.TestCase):
    """Tests for parse_unified_diff()"""

    def test_ruby_diff(self):
        """Parse a Ruby unified diff with insertion and deletion."""
        result = parse_unified_diff(UNIFIED_DIFF_RUBY)
        self.assertIn("app/models/issue.rb", result)
        entry = result["app/models/issue.rb"]
        self.assertEqual(entry["status"], "M")
        self.assertEqual(entry["insertions"], 8)
        self.assertEqual(entry["deletions"], 3)
        self.assertIn("assigned_member || default_assignee", entry["added_lines"][2])
        self.assertTrue(
            any("team_members.find" in l for l in entry["removed_lines"]),
            msg="removed_lines should contain the old implementation",
        )

    def test_new_file(self):
        """Parse a 'new file' diff — status should be 'A'."""
        result = parse_unified_diff(UNIFIED_DIFF_NEW_FILE)
        self.assertIn("app/services/bulk_export.rb", result)
        entry = result["app/services/bulk_export.rb"]
        self.assertEqual(entry["status"], "A")

    def test_deleted_file(self):
        """Parse a 'deleted file' diff — status should be 'D'."""
        result = parse_unified_diff(UNIFIED_DIFF_DELETED_FILE)
        self.assertIn("app/old/legacy_import.rb", result)
        entry = result["app/old/legacy_import.rb"]
        self.assertEqual(entry["status"], "D")
        self.assertEqual(entry["deletions"], 5)

    def test_python_diff(self):
        """Parse a Python diff with import changes."""
        result = parse_unified_diff(UNIFIED_DIFF_PYTHON)
        self.assertIn("src/processor.py", result)
        entry = result["src/processor.py"]
        self.assertGreater(entry["insertions"], 0)
        self.assertGreater(entry["deletions"], 0)
        # Check import lines detected
        added_text = "\n".join(entry["added_lines"])
        removed_text = "\n".join(entry["removed_lines"])
        self.assertIn("import json", added_text)
        self.assertIn("from typing import List", removed_text)

    def test_empty_diff(self):
        """Empty diff string returns empty dict."""
        result = parse_unified_diff("")
        self.assertEqual(result, {})

    def test_multi_file_diff(self):
        """Combined diff with multiple files."""
        combined = UNIFIED_DIFF_RUBY + "\n" + UNIFIED_DIFF_PYTHON
        result = parse_unified_diff(combined)
        self.assertIn("app/models/issue.rb", result)
        self.assertIn("src/processor.py", result)
        self.assertEqual(len(result), 2)


class TestDetectSymbols(unittest.TestCase):
    """Tests for _detect_symbols()"""

    def test_ruby_def(self):
        """Detect Ruby method definitions."""
        lines = [
            "  def assignee",
            "  def self.call",
            "  def assigned_member",
            "  class Issue",
            "  module Exportable",
        ]
        result = _detect_symbols(lines, "app/models/issue.rb")
        self.assertIn("assignee", result["definitions"])
        self.assertIn("call", result["definitions"])
        self.assertIn("assigned_member", result["definitions"])
        self.assertIn("Issue", result["definitions"])
        self.assertIn("Exportable", result["definitions"])

    def test_python_def(self):
        """Detect Python function/class definitions."""
        lines = [
            "def process():",
            "async def fetch():",
            "class Handler:",
            "    def _helper(self):",
        ]
        result = _detect_symbols(lines, "src/handler.py")
        self.assertIn("process", result["definitions"])
        self.assertIn("fetch", result["definitions"])
        self.assertIn("Handler", result["definitions"])
        self.assertIn("_helper", result["definitions"])

    def test_go_func(self):
        """Detect Go function definitions."""
        lines = [
            "func main() {",
            "func (s *Server) Serve() {",
            "func health() bool {",
        ]
        result = _detect_symbols(lines, "cmd/main.go")
        self.assertIn("main", result["definitions"])
        self.assertIn("Serve", result["definitions"])
        self.assertIn("health", result["definitions"])

    def test_js_ts_def(self):
        """Detect JS/TS definitions."""
        lines = [
            "function transform() {",
            "export function render() {",
            "class AppComponent {",
            "const DEFAULT_TIMEOUT = 5000;",
            "let counter = 0;",
            "async function loadData() {",
        ]
        result = _detect_symbols(lines, "src/app.ts")
        self.assertIn("transform", result["definitions"])
        self.assertIn("render", result["definitions"])
        self.assertIn("AppComponent", result["definitions"])
        self.assertIn("DEFAULT_TIMEOUT", result["definitions"])

    def test_import_detection_python(self):
        """Detect Python imports."""
        lines = [
            "import os",
            "from typing import Optional",
            "  import sys  # inline comment",
        ]
        result = _detect_symbols(lines, "src/example.py")
        self.assertIn("os", result["imports"])
        self.assertIn("typing", result["imports"])
        self.assertIn("sys", result["imports"])

    def test_import_detection_ruby(self):
        """Detect Ruby requires."""
        lines = [
            "require 'net/http'",
            "require_relative 'config/environment'",
            "include Comparable",
        ]
        result = _detect_symbols(lines, "app/models/user.rb")
        self.assertIn("net/http", result["imports"])
        self.assertIn("config/environment", result["imports"])
        self.assertIn("Comparable", result["imports"])

    def test_unknown_extension(self):
        """Unknown extension should not crash."""
        lines = ["SOME_VAR = 1"]
        result = _detect_symbols(lines, "Makefile")
        self.assertEqual(result["definitions"], [])
        self.assertEqual(result["imports"], [])


class TestAnalyzeStructuralChanges(unittest.TestCase):
    """Tests for analyze_structural_changes()"""

    def test_ruby_refactor(self):
        """Ruby refactor: split method — new method added, old modified."""
        file_data = parse_unified_diff(UNIFIED_DIFF_RUBY)["app/models/issue.rb"]
        result = analyze_structural_changes(file_data)
        # "assignee" is in both added and removed => modified
        self.assertIn("assignee", result["modified_symbols"])
        # "assigned_member" is only in added symbols
        self.assertIn("assigned_member", result["added_symbols"])

    def test_python_import_changes(self):
        """Python import changes: added json. typing modified (List->Sequence)."""
        file_data = parse_unified_diff(UNIFIED_DIFF_PYTHON)["src/processor.py"]
        result = analyze_structural_changes(file_data)
        self.assertIn("json", result["added_imports"])
        # "typing" is both added (Sequence) and removed (List) → not in net lists

    def test_new_file(self):
        """New file: everything is added."""
        file_data = parse_unified_diff(UNIFIED_DIFF_NEW_FILE)["app/services/bulk_export.rb"]
        result = analyze_structural_changes(file_data)
        self.assertIn("BulkExport", result["added_symbols"])
        self.assertIn("call", result["added_symbols"])

    def test_deleted_file(self):
        """Deleted file: everything is removed."""
        file_data = parse_unified_diff(UNIFIED_DIFF_DELETED_FILE)["app/old/legacy_import.rb"]
        result = analyze_structural_changes(file_data)
        self.assertIn("LegacyImport", result["removed_symbols"])
        self.assertIn("call", result["removed_symbols"])

    def test_empty_change(self):
        """No lines = no structural changes."""
        file_data = {
            "file": "irrelevant.rb",
            "added_lines": [],
            "removed_lines": [],
        }
        result = analyze_structural_changes(file_data)
        self.assertEqual(result["added_symbols"], [])
        self.assertEqual(result["removed_symbols"], [])
        self.assertEqual(result["modified_symbols"], [])
        self.assertEqual(result["added_imports"], [])
        self.assertEqual(result["removed_imports"], [])


class TestExtractSnippets(unittest.TestCase):
    """Tests for extract_snippets()"""

    def test_ruby_snippets(self):
        """Snippet should contain before/after code."""
        file_data = parse_unified_diff(UNIFIED_DIFF_RUBY)["app/models/issue.rb"]
        result = extract_snippets(file_data)
        self.assertIn("team_members.find", result["before_snippet"])
        self.assertIn("assigned_member", result["after_snippet"])

    def test_no_change(self):
        """No added/removed lines => empty snippets."""
        file_data = {
            "file": "empty.rb",
            "added_lines": [],
            "removed_lines": [],
            "context_lines": [],
            "hunks": [],
        }
        result = extract_snippets(file_data)
        self.assertEqual(result["before_snippet"], "")
        self.assertEqual(result["after_snippet"], "")

    def test_new_file(self):
        """New file: before is empty, after has content."""
        file_data = parse_unified_diff(UNIFIED_DIFF_NEW_FILE)["app/services/bulk_export.rb"]
        result = extract_snippets(file_data)
        self.assertEqual(result["before_snippet"], "")
        self.assertIn("BulkExport", result["after_snippet"])


class TestCrossReferenceSections(unittest.TestCase):
    """Tests for cross_reference_sections()"""

    def test_found_section(self):
        """File with SRC-ID that has trace coverage should return sections."""
        # Build source_map with by_path index (as load_source_map does)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sm_path = Path(td) / "sm.json"
            sm_path.write_text(json.dumps(SAMPLE_SOURCE_MAP), encoding="utf-8")
            sm = load_source_map(sm_path)
            sections = cross_reference_sections(
                "app/models/issue.rb", "M",
                sm, SAMPLE_TRACE,
            )
            self.assertEqual(len(sections), 1)
            self.assertEqual(sections[0]["file"], "02-entities.md")
            self.assertEqual(sections[0]["section"], "2.1 Issue")

    def test_no_trace(self):
        """File in source-map but not in trace returns empty list."""
        sections = cross_reference_sections(
            "app/services/bulk_export.rb", "A",
            SAMPLE_SOURCE_MAP, SAMPLE_TRACE,
        )
        self.assertEqual(sections, [])

    def test_not_in_source_map(self):
        """File not in source-map returns empty list."""
        sections = cross_reference_sections(
            "unknown/file.go", "M",
            SAMPLE_SOURCE_MAP, SAMPLE_TRACE,
        )
        self.assertEqual(sections, [])

    def test_empty_source_map(self):
        """Missing source-map returns empty list."""
        sections = cross_reference_sections(
            "app/models/issue.rb", "M",
            {"units": [], "by_path": {}, "by_id": {}},
            SAMPLE_TRACE,
        )
        self.assertEqual(sections, [])


class TestParseNameStatusFromUnified(unittest.TestCase):
    """Tests for _parse_name_status_from_unified()"""

    def test_parse_modify(self):
        """Modified file detected correctly."""
        result = _parse_name_status_from_unified(UNIFIED_DIFF_RUBY)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "M")
        self.assertEqual(result[0]["file"], "app/models/issue.rb")

    def test_parse_new_file(self):
        """New file detected correctly."""
        result = _parse_name_status_from_unified(UNIFIED_DIFF_NEW_FILE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "A")

    def test_parse_deleted_file(self):
        """Deleted file detected correctly."""
        result = _parse_name_status_from_unified(UNIFIED_DIFF_DELETED_FILE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "D")


class TestBuildChangeSpec(unittest.TestCase):
    """Integration tests for build_change_spec()"""

    def test_git_mode_with_inline_diff(self):
        """Build full change-spec from inline unified diff (git mode)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)

            # Write minimal artifacts
            (specback_path / "source-map.json").write_text(
                json.dumps(SAMPLE_SOURCE_MAP), encoding="utf-8",
            )
            (specback_path / "trace.json").write_text(
                json.dumps(SAMPLE_TRACE), encoding="utf-8",
            )

            result = build_change_spec(
                specback_path=specback_path,
                mode=MODE_GIT,
                base="HEAD",
                diff_text=UNIFIED_DIFF_RUBY,
            )

            self.assertEqual(result["schema_version"], "0.1.0")
            self.assertEqual(result["mode"], "git")
            self.assertIn("files", result)
            self.assertGreater(len(result["files"]), 0)
            self.assertIn("summary", result)

            first_file = result["files"][0]
            self.assertIn("file", first_file)
            self.assertIn("structural_changes", first_file)
            self.assertIn("impacted_sections", first_file)
            self.assertIn("before_snippet", first_file)
            self.assertIn("after_snippet", first_file)

    def test_hash_mode_empty_hashes(self):
        """Hash mode with no source-hashes.json reports SM files as new."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            (specback_path / "source-map.json").write_text(
                json.dumps(SAMPLE_SOURCE_MAP), encoding="utf-8",
            )
            (specback_path / "trace.json").write_text(
                json.dumps(SAMPLE_TRACE), encoding="utf-8",
            )
            # No source-hashes.json → no previous state → SM files appear as new
            result = build_change_spec(
                specback_path=specback_path,
                mode=MODE_HASH,
                base="hash-snapshot",
                diff_text=None,
            )
            self.assertEqual(result["summary"]["total_files"], 2)

    def test_no_changes(self):
        """No diff text returns empty result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            (specback_path / "source-map.json").write_text(
                json.dumps(SAMPLE_SOURCE_MAP), encoding="utf-8",
            )
            (specback_path / "trace.json").write_text(
                json.dumps(SAMPLE_TRACE), encoding="utf-8",
            )
            result = build_change_spec(
                specback_path=specback_path,
                mode=MODE_GIT,
                base="HEAD",
                diff_text="",
            )
            self.assertEqual(result["summary"]["total_files"], 0)

    def test_cross_reference_in_output(self):
        """Change files with trace coverage should have impacted_sections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)

            # Include issue.rb which IS in the trace
            (specback_path / "source-map.json").write_text(
                json.dumps(SAMPLE_SOURCE_MAP), encoding="utf-8",
            )
            (specback_path / "trace.json").write_text(
                json.dumps(SAMPLE_TRACE), encoding="utf-8",
            )

            # Diff only issue.rb (which has trace coverage)
            result = build_change_spec(
                specback_path=specback_path,
                mode=MODE_GIT,
                base="HEAD",
                diff_text=UNIFIED_DIFF_RUBY,
            )

            for f in result["files"]:
                if f["file"] == "app/models/issue.rb":
                    self.assertGreater(len(f["impacted_sections"]), 0,
                                       msg="issue.rb should have impacted sections from trace")
                else:
                    self.assertEqual(len(f["impacted_sections"]), 0,
                                     msg=f"Unexpected impacted sections for {f['file']}")


class TestResolveMode(unittest.TestCase):
    """Tests for resolve_mode()"""

    def test_explicit_mode(self):
        """Explicit mode argument should win."""
        self.assertEqual(resolve_mode(MODE_GIT, Path("/nonexistent")), MODE_GIT)
        self.assertEqual(resolve_mode(MODE_HASH, Path("/nonexistent")), MODE_HASH)

    def test_missing_artifacts_fails(self):
        """No artifacts should cause exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            with self.assertRaises(SystemExit):
                resolve_mode(None, specback_path)


class TestResolveBase(unittest.TestCase):
    """Tests for resolve_base()"""

    def test_explicit_base_wins(self):
        """Explicit --base should win over state.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            (specback_path / "state.json").write_text(
                json.dumps({"generated_at_commit": "abc123"}), encoding="utf-8",
            )
            result = resolve_base("v1.0", specback_path)
            self.assertEqual(result, "v1.0")

    def test_state_json_commit(self):
        """If no explicit base, use state.json.generated_at_commit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            (specback_path / "state.json").write_text(
                json.dumps({"generated_at_commit": "def456"}), encoding="utf-8",
            )
            result = resolve_base(None, specback_path)
            self.assertEqual(result, "def456")

    def test_fallback_head(self):
        """Fallback to HEAD when no state.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            specback_path = Path(tmpdir) / ".specback"
            specback_path.mkdir(parents=True)
            result = resolve_base(None, specback_path)
            self.assertEqual(result, "HEAD")


# ---------------------------------------------------------------------------
# Loader delegation (Issue #283 — shared artifact_io.py)
# ---------------------------------------------------------------------------


class TestLoaderDelegation(unittest.TestCase):
    """change-spec's loaders must match artifact_io's shared loaders."""

    def test_load_source_map_matches_artifact_io(self):
        import artifact_io
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "source-map.json"
            p.write_text(json.dumps({
                "units": [
                    {"id": "SRC-0001", "path": "a.py"},
                    {"id": "SRC-0002", "path": "b.py"},
                ],
                "stats": {"files_scanned": 2},
                "target_root": "repo",
            }), encoding="utf-8")
            self.assertEqual(
                load_source_map(p),
                artifact_io.load_source_map(p, build_indexes=True),
            )

    def test_load_source_map_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                load_source_map(Path(td) / "nope.json"),
                {"units": [], "by_path": {}, "by_id": {}, "stats": {}, "target_root": ""},
            )

    def test_load_trace_matches_artifact_io(self):
        import artifact_io
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.json"
            p.write_text(json.dumps({"by_source": {"SRC-1": {"path": "a.py"}}}),
                         encoding="utf-8")
            self.assertEqual(load_trace(p), artifact_io.load_trace(p))

    def test_load_trace_missing_returns_empty_by_source(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_trace(Path(td) / "nope.json"), {"by_source": {}})

    def test_load_state_matches_artifact_io(self):
        import artifact_io
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text(json.dumps({"generated_at_commit": "abc123"}), encoding="utf-8")
            self.assertEqual(load_state(p), artifact_io.load_state(p))
            self.assertIsNone(load_state(Path(td) / "missing.json"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
