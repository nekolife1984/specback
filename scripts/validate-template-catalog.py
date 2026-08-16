#!/usr/bin/env python3
"""
validate-template-catalog.py — Verify templates/catalog.json stays in sync
with the template files it describes.

The catalog is the machine-readable registry consumed by phase-1 recon and
other tooling. This script checks that every entry in catalog.json matches
its corresponding `templates/<name>.md` file:

1.  File existence — every catalog entry has a template file, and every
    template file (excluding `templates/ci/`) has a catalog entry.
2.  Frontmatter identity — `template_name` / `description` / `template_version`
    in the template frontmatter match the catalog entry.
3.  Chapter outline — the `### Chapter N: Title` headings in the template
    match `chapters` in the catalog (order matters).
4.  Detection rules — `always_include` / `chapters` / `extra_chapters` /
    `optional` extracted from the template's `detection_rules` block match
    the catalog's `detection_rules`.
5.  REF format — the template body uses the HTML-comment REF form
    (`<!-- REF: ... -->`); the deprecated `[REF: ...]` form is rejected.
6.  Languages — each `languages` entry is one of the languages supported by
    source_map_v2 extractors.

Exit codes:
  0 — catalog and templates are consistent
  1 — violations found
  2 — usage error (bad args, missing/invalid files)

Usage:
    python3 validate-template-catalog.py [--catalog templates/catalog.json]
                                         [--templates-dir templates]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import reject_nonfinite, sanitize_control

# Languages registered by source_map_v2/extractors/*_ext.py (`language = ...`).
# Kept as a local set so this script stays dependency-free (it does not import
# source_map_v2, which may pull tree-sitter). Update when extractors change.
SUPPORTED_LANGUAGES = {
    "c", "cobol", "cpp", "csharp", "dart", "go", "java", "javascript",
    "kotlin", "php", "python", "ruby", "rust", "sql", "swift", "typescript",
}

# Template names are lowercase-kebab; whitelist rejects path separators,
# "..", control characters, and other names that would escape templates_dir.
TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

FRONTMATTER_START_RE = re.compile(r"^(?:\|?---)\n")  # web-app.md uses "|---"
# Greedy `(.+)` is intentional: lazy `(.+?)` + trailing `\s*` backtracks
# quadratically on a line with a long whitespace run (ReDoS, confirmed
# 36 s at 100 KB). Greedy `.+` consumes to end-of-line in one pass.
CHAPTER_HEADING_RE = re.compile(r"^### Chapter \d+: (.+)\s*$", re.MULTILINE)
REF_BRACKET_RE = re.compile(r"\[REF:")
TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:")
OPTIONAL_RE = re.compile(r"^\s*optional:\s*true")


class CatalogError(Exception):
    """Raised when a template/catalog inconsistency is found."""


def load_catalog(catalog_path: Path) -> dict[str, Any]:
    """Load and validate the catalog JSON structure."""
    try:
        text = catalog_path.read_text(encoding="utf-8")
        data = json.loads(text, parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, OSError, ValueError, RecursionError) as exc:
        raise CatalogError(f"{catalog_path}: cannot parse catalog: {exc}") from exc

    if not isinstance(data, dict) or "templates" not in data:
        raise CatalogError(f"{catalog_path}: missing top-level 'templates' list")
    templates = data["templates"]
    if not isinstance(templates, list):
        raise CatalogError(f"{catalog_path}: 'templates' must be a list")

    by_name: dict[str, dict[str, Any]] = {}
    for entry in templates:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise CatalogError(f"{catalog_path}: each template entry needs a 'name' string")
        if not TEMPLATE_NAME_RE.fullmatch(entry["name"]):
            raise CatalogError(
                f"{catalog_path}: invalid template name {sanitize_control(entry['name'])!r} "
                f"(allowed: [A-Za-z0-9][A-Za-z0-9._-]*)"
            )
        if entry["name"] in by_name:
            raise CatalogError(f"{catalog_path}: duplicate template name '{entry['name']}'")
        by_name[entry["name"]] = entry
    return {"by_name": by_name}


def extract_frontmatter(template_path: Path) -> dict[str, str]:
    """Extract the YAML frontmatter block of a template file.

    Returns ``{"text": ..., "body": ...}`` on success, ``{"error": ...}`` when
    the frontmatter is unterminated, or ``{}`` when there is no frontmatter.
    Raises ``CatalogError`` on unreadable / non-UTF-8 files.
    """
    try:
        text = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"{template_path}: cannot read template: {exc}") from exc
    if not FRONTMATTER_START_RE.match(text):
        return {}
    # frontmatter ends at the next line that is exactly "---" (or "|---" followed by nothing)
    lines = text.split("\n")
    # skip the opening marker line (line 0)
    end = None
    for i in range(1, len(lines)):
        if re.match(r"^\|?---\s*$", lines[i]):
            end = i
            break
    if end is None:
        return {"error": "unterminated frontmatter (missing closing ---)"}
    return {"text": "\n".join(lines[1:end]), "body": "\n".join(lines[end + 1:])}


def _frontmatter_field(fm: dict[str, str], key: str) -> str | None:
    """Read a scalar top-level field from the frontmatter text.

    Strips one level of matching quotes (single-line YAML scalars).
    ``[^\\S\\n]`` (horizontal whitespace only) keeps ``\\s*`` from crossing
    newlines when the value is empty.
    """
    m = re.search(rf"^{key}:[^\S\n]*(.+?)[^\S\n]*$", fm.get("text", ""), re.M)
    if not m:
        return None
    val = m.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def extract_detection_rules(fm: dict[str, str]) -> dict[str, list[str]]:
    """Extract always_include / chapters / extra_chapters / optional id lists.

    Only the canonical block form is accepted: ``detection_rules:`` followed by
    the four 2-space-indented keys, with ``- id: ch-...`` items at 4-space
    indent and ``optional: true`` inside a chapter block. Once the block ends
    (a top-level key or the frontmatter end), later ``- id:`` lines are
    ignored — state cannot leak from a subsequent section (post-review #299).
    """
    rules: dict[str, list[str]] = {"always_include": [], "chapters": [], "extra_chapters": [], "optional": []}
    current: str | None = None
    last_id: str | None = None
    in_detection_rules = False
    for line in fm.get("text", "").split("\n"):
        if re.match(r"^detection_rules:", line):
            in_detection_rules = True
            current = "detection"
            continue
        if not in_detection_rules:
            continue
        if TOP_LEVEL_KEY_RE.match(line) and not line.startswith("  "):
            # a new top-level key ends the detection_rules block
            in_detection_rules = False
            current = None
            continue
        if re.match(r"^  always_include:", line):
            current = "always_include"
            last_id = None
            continue
        if re.match(r"^  chapters:", line):
            current = "chapters"
            last_id = None
            continue
        if re.match(r"^  extra_chapters:", line):
            current = "extra_chapters"
            last_id = None
            continue
        if re.match(r"^  granularity:", line):
            current = "granularity"
            last_id = None
            continue
        if current == "always_include":
            m = re.match(r"^    - (ch-[a-z0-9-]+)$", line)
            if m:
                rules["always_include"].append(m.group(1))
        elif current in ("chapters", "extra_chapters"):
            m = re.match(r"^    - id: (ch-[a-z0-9-]+)$", line)
            if m:
                last_id = m.group(1)
                rules[current].append(last_id)
                continue
            if OPTIONAL_RE.match(line) and last_id:
                rules["optional"].append(last_id)
    return rules


def extract_chapter_titles(body: str) -> list[str]:
    """Extract `### Chapter N: Title` headings in order (titles stripped)."""
    return [m.group(1).strip() for m in CHAPTER_HEADING_RE.finditer(body)]


def find_template_files(templates_dir: Path) -> list[Path]:
    """Return template .md files at the top level of the directory.

    Non-recursive glob; subdirectories (e.g. ``ci/``) are naturally excluded.
    Symlinks are followed by ``is_file()`` here but rejected in
    ``validate_entry`` (path containment check).
    """
    return sorted(p for p in templates_dir.glob("*.md") if p.is_file())


def validate_entry(
    entry: dict[str, Any],
    templates_dir: Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate one catalog entry against its template file."""
    name = entry["name"]
    template_path = templates_dir / f"{name}.md"

    # path containment / symlink defense (post-review #299): the name is
    # already whitelisted in load_catalog; this is defense-in-depth.
    # Check symlink first — resolve() on a symlink already follows it, so the
    # "escapes templates dir" error would mask the symlink finding.
    if template_path.is_symlink():
        errors.append(f"catalog entry '{name}': template file must not be a symlink")
        return
    resolved = template_path.resolve()
    if not resolved.is_relative_to(templates_dir.resolve()):
        errors.append(
            f"catalog entry '{name}': template path escapes templates dir: {template_path}"
        )
        return

    if not template_path.is_file():
        errors.append(f"catalog entry '{name}': template file missing: {template_path}")
        return

    fm = extract_frontmatter(template_path)
    if "error" in fm:
        errors.append(f"{template_path}: {fm['error']}")
        return
    if not fm:
        errors.append(f"{template_path}: no YAML frontmatter found")
        return

    # 2. frontmatter identity
    fm_name = _frontmatter_field(fm, "template_name")
    if fm_name != name:
        errors.append(
            f"{template_path}: template_name {sanitize_control(str(fm_name))!r} "
            f"!= catalog name {sanitize_control(name)!r}"
        )
    desc = _frontmatter_field(fm, "description")
    if desc is None and entry.get("description") is not None:
        warnings.append(f"{template_path}: description missing from frontmatter but present in catalog")
    elif desc is not None and entry.get("description") is not None and desc != entry["description"]:
        errors.append(f"{template_path}: description differs from catalog entry '{name}'")
    ver = _frontmatter_field(fm, "template_version")
    if ver is None and entry.get("template_version") is not None:
        warnings.append(f"{template_path}: template_version missing from frontmatter but present in catalog")
    elif ver is not None and entry.get("template_version") is not None and ver != entry["template_version"]:
        errors.append(
            f"{template_path}: template_version {sanitize_control(str(ver))!r} "
            f"!= catalog {sanitize_control(str(entry.get('template_version')))!r}"
        )

    # 3. chapter outline
    titles = extract_chapter_titles(fm["body"])
    cat_chapters = entry.get("chapters", [])
    if titles != cat_chapters:
        errors.append(
            f"{template_path}: chapter outline mismatch. "
            f"Template has {len(titles)} chapters, catalog has {len(cat_chapters)}. "
            f"template-only: {sorted(set(titles) - set(cat_chapters))}, "
            f"catalog-only: {sorted(set(cat_chapters) - set(titles))}"
        )

    # 4. detection rules
    template_rules = extract_detection_rules(fm)
    cat_rules = entry.get("detection_rules", {})
    if not isinstance(cat_rules, dict):
        errors.append(f"catalog entry '{name}': detection_rules must be an object")
        return
    for key in ("always_include", "chapters", "extra_chapters", "optional"):
        template_list = template_rules.get(key, [])
        catalog_list = cat_rules.get(key, [])
        if not isinstance(catalog_list, list):
            errors.append(f"catalog entry '{name}': detection_rules.{key} must be a list")
            continue
        if template_list != catalog_list:
            errors.append(
                f"{template_path}: detection_rules.{key} mismatch. "
                f"template={template_list}, catalog={catalog_list}"
            )

    # 5. REF format — deprecated bracket form must be absent
    body = fm["body"]
    if REF_BRACKET_RE.search(body):
        errors.append(
            f"{template_path}: deprecated '[REF: ...]' form found. "
            f"Use HTML-comment form '<!-- REF: ... -->' instead."
        )

    # 6. languages
    languages = entry.get("languages", [])
    if not isinstance(languages, list):
        errors.append(f"catalog entry '{name}': 'languages' must be a list")
        return
    for lang in languages:
        if lang not in SUPPORTED_LANGUAGES:
            errors.append(
                f"catalog entry '{name}': unknown language {sanitize_control(str(lang))!r}. "
                f"Supported: {sorted(SUPPORTED_LANGUAGES)}"
            )


def validate(catalog_path: Path, templates_dir: Path) -> tuple[list[str], list[str], int]:
    """Run full validation. Returns (errors, warnings, catalog_entry_count)."""
    errors: list[str] = []
    warnings: list[str] = []

    catalog = load_catalog(catalog_path)
    by_name = catalog["by_name"]

    # Every template file must have a catalog entry
    for tf in find_template_files(templates_dir):
        stem = tf.stem
        if stem not in by_name:
            errors.append(f"{tf}: no catalog entry for template '{stem}'")

    # Every catalog entry must match its template file
    for entry in catalog["by_name"].values():
        validate_entry(entry, templates_dir, errors, warnings)

    return errors, warnings, len(by_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate templates/catalog.json against template files.",
    )
    parser.add_argument(
        "--catalog",
        default="templates/catalog.json",
        help="Path to the catalog JSON (default: templates/catalog.json)",
    )
    parser.add_argument(
        "--templates-dir",
        default="templates",
        help="Directory containing template .md files (default: templates)",
    )
    args = parser.parse_args(argv)

    catalog_path = Path(args.catalog)
    templates_dir = Path(args.templates_dir)

    if not catalog_path.is_file():
        print(f"ERROR: catalog file not found: {catalog_path}", file=sys.stderr)
        return 2
    if not templates_dir.is_dir():
        print(f"ERROR: templates dir not found: {templates_dir}", file=sys.stderr)
        return 2

    try:
        errors, warnings, catalog_count = validate(catalog_path, templates_dir)
    except CatalogError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        print(f"\n❌ {len(errors)} violation(s) — catalog and templates are out of sync.", file=sys.stderr)
        return 1

    print(f"✅ catalog.json is consistent with {catalog_count} template(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
