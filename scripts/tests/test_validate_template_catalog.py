"""Tests for validate-template-catalog.py.

Validates that templates/catalog.json stays in sync with the template files.
Uses importlib loading (hyphen-free module name) and inline fixture templates
so tests run standalone without polluting the real templates/ dir.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "validate-template-catalog.py"


def _load_mod():
    """Load validate-template-catalog.py as a module via importlib."""
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("vtc_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vtc_test"] = mod
    spec.loader.exec_module(mod)
    return mod


vtc = _load_mod()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_FRONTMATTER = """---
template_name: demo
template_version: 1.2.3
description: A demo template for tests.
reader_order:
  maintenance_developer: null
detection_rules:
  always_include:
    - ch-overview
    - ch-design-decisions
  chapters:
    - id: ch-auth
      title: Authentication
      slug: 02-auth
      detection:
        patterns:
          - rgs: ["auth", "jwt"]
        note_missing: "Auth not found"
    - id: ch-optional-one
      title: Optional chapter
      slug: 03-optional-one
      detection:
        patterns:
          - rgs: ["opt"]
        note_missing: "Not found"
        optional: true
  extra_chapters:
    - id: ch-webhooks
      title: Webhooks
      slug: 04-webhooks
      detection:
        patterns:
          - rgs: ["webhook"]
        note_detected: "Webhook detected"
  granularity:
    merge: []
    split: []
---

# Demo template

## Chapter outline

### Chapter 1: Overview

### Chapter 2: Feature specifications

### Chapter 3: Authentication

### Chapter 4: Optional chapter

### Chapter 5: Design decisions

### Chapter 6: Known constraints and unresolved items

<!-- REF: SRC-0001 -->
"""


def _catalog_entry(
    name: str = "demo",
    chapters: list[str] | None = None,
    rules: dict | None = None,
    languages: list[str] | None = None,
    version: str = "1.2.3",
    description: str = "A demo template for tests.",
) -> dict:
    return {
        "name": name,
        "description": description,
        "template_version": version,
        "chapters": chapters
        or [
            "Overview",
            "Feature specifications",
            "Authentication",
            "Optional chapter",
            "Design decisions",
            "Known constraints and unresolved items",
        ],
        "detection_rules": rules
        or {
            "always_include": ["ch-overview", "ch-design-decisions"],
            "chapters": ["ch-auth", "ch-optional-one"],
            "extra_chapters": ["ch-webhooks"],
            "optional": ["ch-optional-one"],
        },
        "languages": languages or ["python", "typescript"],
    }


@pytest.fixture
def demo_template(tmp_path: Path) -> Path:
    p = tmp_path / "templates"
    p.mkdir()
    (p / "demo.md").write_text(VALID_FRONTMATTER, encoding="utf-8")
    return p


@pytest.fixture
def demo_catalog(tmp_path: Path) -> Path:
    p = tmp_path / "catalog.json"
    p.write_text(
        json.dumps({"schema_version": "1.0.0", "templates": [_catalog_entry()]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# extract_frontmatter
# ---------------------------------------------------------------------------

def test_extract_frontmatter_standard_marks() -> None:
    # frontmatter start regex accepts both "---" and the web-app "|---" quirk
    assert vtc.FRONTMATTER_START_RE.match("---\ntemplate_name: x\n")
    assert vtc.FRONTMATTER_START_RE.match("|---\ntemplate_name: x\n")  # web-app quirk
    assert not vtc.FRONTMATTER_START_RE.match("# no frontmatter\n")


def test_extract_frontmatter_parses_body(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_text(VALID_FRONTMATTER, encoding="utf-8")
    fm = vtc.extract_frontmatter(p)
    assert "template_name" in fm["text"]
    assert "## Chapter outline" in fm["body"]
    assert "### Chapter 1: Overview" in fm["body"]


def test_extract_frontmatter_missing(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_text("# no frontmatter\n", encoding="utf-8")
    assert vtc.extract_frontmatter(p) == {}


def test_frontmatter_field_scalar() -> None:
    fm = {"text": "template_name: demo\ntemplate_version: 1.2.3\n"}
    assert vtc._frontmatter_field(fm, "template_name") == "demo"
    assert vtc._frontmatter_field(fm, "template_version") == "1.2.3"
    assert vtc._frontmatter_field(fm, "missing") is None


# ---------------------------------------------------------------------------
# extract_detection_rules
# ---------------------------------------------------------------------------

def test_extract_detection_rules_lists() -> None:
    fm = {"text": VALID_FRONTMATTER.split("\n---\n", 1)[0].removeprefix("---\n")}
    rules = vtc.extract_detection_rules(fm)
    assert rules["always_include"] == ["ch-overview", "ch-design-decisions"]
    assert rules["chapters"] == ["ch-auth", "ch-optional-one"]
    assert rules["extra_chapters"] == ["ch-webhooks"]
    assert rules["optional"] == ["ch-optional-one"]


def test_extract_detection_rules_empty() -> None:
    rules = vtc.extract_detection_rules({"text": ""})
    assert rules == {"always_include": [], "chapters": [], "extra_chapters": [], "optional": []}


# ---------------------------------------------------------------------------
# extract_chapter_titles
# ---------------------------------------------------------------------------

def test_extract_chapter_titles_order() -> None:
    body = "## Chapter outline\n\n### Chapter 1: Overview\n\n### Chapter 2: Feature specifications\n"
    assert vtc.extract_chapter_titles(body) == ["Overview", "Feature specifications"]


def test_extract_chapter_titles_multiline() -> None:
    # ^ must match at line starts (re.MULTILINE) — regression for the v1 bug
    body = "# t\n\n### Chapter 1: Overview\n"
    assert vtc.extract_chapter_titles(body) == ["Overview"]


# ---------------------------------------------------------------------------
# find_template_files
# ---------------------------------------------------------------------------

def test_find_template_files_excludes_ci(tmp_path: Path) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "a.md").write_text("---\ntemplate_name: a\n---\n", encoding="utf-8")
    (tdir / "b.md").write_text("---\ntemplate_name: b\n---\n", encoding="utf-8")
    (tdir / "ci").mkdir()
    (tdir / "ci" / "x.md").write_text("ignored", encoding="utf-8")
    names = [p.stem for p in vtc.find_template_files(tdir)]
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# validate — happy path and violations
# ---------------------------------------------------------------------------

def test_validate_consistent(demo_template: Path, demo_catalog: Path) -> None:
    errors, warnings, count = vtc.validate(demo_catalog, demo_template)
    assert errors == []
    assert warnings == []
    assert count == 1


def test_validate_missing_template_file(demo_template: Path, demo_catalog: Path) -> None:
    (demo_template / "demo.md").unlink()
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("template file missing" in e for e in errors)


def test_validate_orphan_template(demo_template: Path, demo_catalog: Path) -> None:
    (demo_template / "extra.md").write_text(
        "---\ntemplate_name: extra\n---\n### Chapter 1: X\n", encoding="utf-8"
    )
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("no catalog entry" in e for e in errors)


def test_validate_chapter_mismatch(demo_template: Path, demo_catalog: Path) -> None:
    data = json.loads(demo_catalog.read_text(encoding="utf-8"))
    data["templates"][0]["chapters"] = data["templates"][0]["chapters"][:-1]
    demo_catalog.write_text(json.dumps(data), encoding="utf-8")
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("chapter outline mismatch" in e for e in errors)


def test_validate_detection_rules_mismatch(demo_template: Path, demo_catalog: Path) -> None:
    data = json.loads(demo_catalog.read_text(encoding="utf-8"))
    data["templates"][0]["detection_rules"]["chapters"] = ["ch-auth", "ch-wrong"]
    demo_catalog.write_text(json.dumps(data), encoding="utf-8")
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("detection_rules.chapters mismatch" in e for e in errors)


def test_validate_version_mismatch(demo_template: Path, demo_catalog: Path) -> None:
    data = json.loads(demo_catalog.read_text(encoding="utf-8"))
    data["templates"][0]["template_version"] = "9.9.9"
    demo_catalog.write_text(json.dumps(data), encoding="utf-8")
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("template_version" in e for e in errors)


def test_validate_unknown_language(demo_template: Path, demo_catalog: Path) -> None:
    data = json.loads(demo_catalog.read_text(encoding="utf-8"))
    data["templates"][0]["languages"] = ["python", "not-a-lang"]
    demo_catalog.write_text(json.dumps(data), encoding="utf-8")
    errors, _, _ = vtc.validate(demo_catalog, demo_template)
    assert any("unknown language 'not-a-lang'" in e for e in errors)


def test_validate_bracket_ref_rejected(tmp_path: Path) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    bad = VALID_FRONTMATTER.replace("<!-- REF: SRC-0001 -->", "[REF: SRC-0001]")
    (tdir / "demo.md").write_text(bad, encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"templates": [_catalog_entry()]}), encoding="utf-8"
    )
    errors, _, _ = vtc.validate(catalog, tdir)
    assert any("deprecated '[REF: ...]' form" in e for e in errors)


def test_validate_duplicate_name(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"templates": [_catalog_entry(), _catalog_entry()]}), encoding="utf-8"
    )
    with pytest.raises(vtc.CatalogError, match="duplicate template name"):
        vtc.validate(catalog, tmp_path / "templates")


def test_validate_bad_json(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{ not json", encoding="utf-8")
    with pytest.raises(vtc.CatalogError, match="cannot parse catalog"):
        vtc.validate(catalog, tmp_path / "templates")


def test_validate_missing_templates_key(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(vtc.CatalogError, match="missing top-level 'templates'"):
        vtc.validate(catalog, tmp_path / "templates")


# ---------------------------------------------------------------------------
# load_catalog / validate_entry direct calls (coverage gate requires
# module-attribute references, not just function-name occurrences)
# ---------------------------------------------------------------------------

def test_load_catalog_returns_by_name(demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    assert "demo" in catalog["by_name"]
    assert catalog["by_name"]["demo"]["template_version"] == "1.2.3"


def test_load_catalog_duplicate(demo_catalog: Path) -> None:
    data = json.loads(demo_catalog.read_text(encoding="utf-8"))
    data["templates"].append(_catalog_entry())
    demo_catalog.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(vtc.CatalogError, match="duplicate template name"):
        vtc.load_catalog(demo_catalog)


def test_validate_entry_consistent(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(catalog["by_name"]["demo"], demo_template, errors, warnings)
    assert errors == []


def test_validate_entry_name_mismatch(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    entry = catalog["by_name"]["demo"]
    # mutate the template frontmatter's template_name, not the entry name
    (demo_template / "demo.md").write_text(
        VALID_FRONTMATTER.replace("template_name: demo", "template_name: other"), encoding="utf-8"
    )
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(entry, demo_template, errors, warnings)
    assert any("template_name 'other' != catalog name 'demo'" in e for e in errors)


def test_validate_entry_bad_detection_rules_type(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    entry = dict(catalog["by_name"]["demo"])
    entry["detection_rules"] = "not-a-dict"
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(entry, demo_template, errors, warnings)
    assert any("detection_rules must be an object" in e for e in errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_missing_catalog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = vtc.main(["--catalog", str(tmp_path / "nope.json"), "--templates-dir", str(tmp_path)])
    assert rc == 2
    assert "catalog file not found" in capsys.readouterr().err


def test_cli_missing_templates_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = vtc.main(["--catalog", str(tmp_path / "c.json"), "--templates-dir", str(tmp_path / "nope")])
    assert rc == 2


def test_cli_ok(demo_template: Path, demo_catalog: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = vtc.main(["--catalog", str(demo_catalog), "--templates-dir", str(demo_template)])
    assert rc == 0
    assert "consistent with 1 template(s)" in capsys.readouterr().out


def test_cli_violation(demo_template: Path, demo_catalog: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (demo_template / "demo.md").unlink()
    rc = vtc.main(["--catalog", str(demo_catalog), "--templates-dir", str(demo_template)])
    assert rc == 1
    assert "violation(s)" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Real-repo integration (repo templates dir; skipped when not in the repo)
# ---------------------------------------------------------------------------

REPO_ROOT = SCRIPT.parent.parent


@pytest.mark.skipif(not (REPO_ROOT / "templates" / "catalog.json").is_file(), reason="repo templates absent")
def test_repo_catalog_consistent() -> None:
    errors, _, count = vtc.validate(REPO_ROOT / "templates" / "catalog.json", REPO_ROOT / "templates")
    assert errors == []
    # entry count must equal the number of template files (not a hardcoded 9)
    assert count == len(list((REPO_ROOT / "templates").glob("*.md")))


@pytest.mark.skipif(not (REPO_ROOT / "templates" / "catalog.json").is_file(), reason="repo templates absent")
def test_repo_catalog_languages_subset() -> None:
    data = json.loads((REPO_ROOT / "templates" / "catalog.json").read_text(encoding="utf-8"))
    for entry in data["templates"]:
        for lang in entry["languages"]:
            assert lang in vtc.SUPPORTED_LANGUAGES, f"{entry['name']}: {lang}"


# ---------------------------------------------------------------------------
# Post-review hardening regression tests (security + code review, #299 follow-up)
# ---------------------------------------------------------------------------

def test_load_catalog_rejects_invalid_name(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"templates": [_catalog_entry(name="../secret", version="x", description="x")]}),
        encoding="utf-8",
    )
    with pytest.raises(vtc.CatalogError, match="invalid template name"):
        vtc.load_catalog(catalog)


def test_load_catalog_rejects_nan(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"templates": [{"name": "demo", "template_version": NaN}]}', encoding="utf-8"
    )
    with pytest.raises(vtc.CatalogError, match="cannot parse catalog"):
        vtc.load_catalog(catalog)


def test_load_catalog_rejects_non_utf8(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(b'{"templates": [\xff\xfe]}')
    with pytest.raises(vtc.CatalogError, match="cannot parse catalog"):
        vtc.load_catalog(catalog)


def test_load_catalog_rejects_deep_nesting(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(vtc.CatalogError):
        vtc.load_catalog(catalog)


def test_extract_frontmatter_non_utf8_raises(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_bytes(b"---\ntemplate_name: \xff\xfe\n---\n")
    with pytest.raises(vtc.CatalogError, match="cannot read template"):
        vtc.extract_frontmatter(p)


def test_extract_frontmatter_unterminated(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_text("---\ntemplate_name: demo\n", encoding="utf-8")
    fm = vtc.extract_frontmatter(p)
    assert "unterminated frontmatter" in fm.get("error", "")


def test_extract_frontmatter_crlf(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_text("---\r\ntemplate_name: demo\r\n---\r\nbody\r\n", encoding="utf-8")
    fm = vtc.extract_frontmatter(p)
    assert "template_name" in fm["text"]
    assert "body" in fm["body"]


def test_frontmatter_field_quoted_scalar() -> None:
    fm = {"text": 'template_name: "demo"\ndescription: \'A test\'\ntemplate_version: "1.2.3"\n'}
    assert vtc._frontmatter_field(fm, "template_name") == "demo"
    assert vtc._frontmatter_field(fm, "description") == "A test"
    assert vtc._frontmatter_field(fm, "template_version") == "1.2.3"


def test_frontmatter_field_empty_does_not_cross_lines() -> None:
    fm = {"text": "description:\ndetection_rules:\n"}
    # empty description must not swallow the next line — treated as missing
    assert vtc._frontmatter_field(fm, "description") is None


def test_extract_detection_rules_no_pollution_after_block() -> None:
    # a later top-level section with "- id:" must NOT leak into extra_chapters
    fm = {"text": (
        "detection_rules:\n"
        "  always_include:\n"
        "    - ch-overview\n"
        "  chapters:\n"
        "    - id: ch-auth\n"
        "  extra_chapters:\n"
        "    - id: ch-webhooks\n"
        "other_section:\n"
        "    - id: ch-sneaky\n"
    )}
    rules = vtc.extract_detection_rules(fm)
    assert rules["extra_chapters"] == ["ch-webhooks"]


def test_extract_detection_rules_optional_indent_flexible() -> None:
    fm = {"text": (
        "detection_rules:\n"
        "  chapters:\n"
        "    - id: ch-auth\n"
        "      optional: true\n"
    )}
    rules = vtc.extract_detection_rules(fm)
    assert rules["optional"] == ["ch-auth"]


def test_validate_entry_languages_non_list(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    entry = dict(catalog["by_name"]["demo"])
    entry["languages"] = "python"
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(entry, demo_template, errors, warnings)
    assert any("'languages' must be a list" in e for e in errors)


def test_validate_entry_escape_sequence_sanitized(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    entry = catalog["by_name"]["demo"]
    # real ESC/OSC control bytes in the template frontmatter
    evil = "template_name: \x1b]0;OWNED\x07\x1b[31mRED\x1b[0m"
    (demo_template / "demo.md").write_text(
        VALID_FRONTMATTER.replace("template_name: demo", evil),
        encoding="utf-8",
    )
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(entry, demo_template, errors, warnings)
    # raw control bytes must not reach the error output (sanitized)
    assert not any("\x1b" in e or "\x07" in e for e in errors)
    assert any("!= catalog name" in e for e in errors)


def test_validate_entry_missing_description_warns(demo_template: Path, demo_catalog: Path) -> None:
    catalog = vtc.load_catalog(demo_catalog)
    entry = catalog["by_name"]["demo"]
    (demo_template / "demo.md").write_text(
        VALID_FRONTMATTER.replace("description: A demo template for tests.\n", ""),
        encoding="utf-8",
    )
    errors: list[str] = []
    warnings: list[str] = []
    vtc.validate_entry(entry, demo_template, errors, warnings)
    assert any("description missing from frontmatter" in w for w in warnings)


def test_validate_entry_symlink_rejected(tmp_path: Path) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    target = tmp_path / "secret.md"
    target.write_text("---\ntemplate_name: demo\n---\n### Chapter 1: X\n", encoding="utf-8")
    (tdir / "demo.md").symlink_to(target)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"templates": [_catalog_entry()]}), encoding="utf-8")
    errors, _, _ = vtc.validate(catalog, tdir)
    assert any("must not be a symlink" in e for e in errors)


def test_chapter_heading_no_redos() -> None:
    # long whitespace run must not cause quadratic backtracking (was 36s at 100KB)
    import time
    body = "### Chapter 1: " + " " * 200_000 + "X\n"
    t0 = time.monotonic()
    titles = vtc.extract_chapter_titles(body)
    elapsed = time.monotonic() - t0
    assert titles == ["X"]
    assert elapsed < 2.0, f"chapter heading extraction took {elapsed:.2f}s (ReDoS regression)"


def test_extract_detection_rules_block_ends_at_next_key() -> None:
    fm = {"text": (
        "detection_rules:\n"
        "  always_include:\n"
        "    - ch-overview\n"
        "reader_order:\n"
        "  maintenance_developer: null\n"
    )}
    rules = vtc.extract_detection_rules(fm)
    assert rules["always_include"] == ["ch-overview"]
