# Template Catalog (templates/catalog.json)

> **Documentation**: [English](11-template-catalog.md) · [日本語](../ja/11-template-catalog.md)

## Overview

The 9 spec templates ship with `detection_rules` (auto-detection rules for
chapter customisation, see issue #186) embedded in each template's YAML
frontmatter. Before issue #299 there was no **machine-readable registry** of
templates: an agent doing phase-1 recon had to open template files by hand to
decide which template fits a codebase.

`templates/catalog.json` is that registry. It is a single JSON file listing
every template's:

| Field | Meaning |
|-------|---------|
| `name` | Template name — matches `templates/<name>.md` and the `template_name` frontmatter field |
| `description` | One-line description, synced from the template frontmatter |
| `template_version` | Template version, synced from the template frontmatter |
| `chapters` | Ordered chapter outline — synced from the `### Chapter N: Title` headings in the template |
| `detection_rules` | `always_include` / `chapters` / `extra_chapters` / `optional` id lists, extracted from the template's `detection_rules` frontmatter block |
| `languages` | Languages the template targets, from the source_map_v2 extractor set (`SUPPORTED_LANGUAGES`) |

The catalog is **canonical**: when the template files change, the catalog must
be updated in the same change, and vice versa.

## Validation

`scripts/validate-template-catalog.py` keeps the catalog and the template
files in sync:

```bash
python3 scripts/validate-template-catalog.py
# ✅ catalog.json is consistent with 9 template(s).
```

It checks:

1. **File existence** — every catalog entry has a template file, and every
   `templates/*.md` (excluding `templates/ci/`) has a catalog entry.
2. **Frontmatter identity** — `template_name` / `description` /
   `template_version` match.
3. **Chapter outline** — the `### Chapter N: Title` headings match the
   `chapters` list (order matters).
4. **Detection rules** — `always_include` / `chapters` / `extra_chapters` /
   `optional` id lists match the template's `detection_rules` block.
5. **REF format** — the deprecated `[REF: ...]` bracket form is rejected;
   templates must use the HTML-comment form `<!-- REF: ... -->`.
6. **Languages** — every `languages` entry is a supported extractor language.

Exit codes: `0` consistent, `1` violations found, `2` usage error.

The validator is wired into the pre-commit hook (Phase 4 — runs whenever
`templates/catalog.json` or a `templates/*.md` file is staged) and into CI
(`Validate template catalog sync` step), so drift is caught before merge.

Hardening (post-review of PR #300): the chapter-heading regex is greedy to
avoid quadratic backtracking (ReDoS); non-UTF-8 files, deep/NaN JSON, and
invalid template names (`../`, separators, control chars) fail cleanly with
exit 2; template files must not be symlinks and must resolve inside the
templates dir; control characters in interpolated values are sanitized;
quoted YAML scalars and CRLF line endings are handled; a missing
`description` / `template_version` in the frontmatter emits a warning
(not an error) when the catalog has a value.

## Usage in phase-1 recon

`skills/specback/phases/phase-1-recon.md` step 2 reads the catalog to
shortlist template candidates — each entry's `languages` can be matched
against the codebase's detected language mix before presenting candidates to
the user. Step 3a reads `detection_rules` from the catalog entry (or the
template frontmatter, which is kept identical by the validator).

## Adding or changing a template

1. Edit `templates/<name>.md` (frontmatter `description` /
   `template_version` / `detection_rules`, or chapter headings).
2. Update the matching entry in `templates/catalog.json` (`description` /
   `template_version` / `chapters` / `detection_rules` / `languages`).
3. Run `python3 scripts/validate-template-catalog.py` — it must pass with
   exit `0` before committing.
