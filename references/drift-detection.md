# Drift Detection — Design Document

## Overview

Phase 7 (Drift Detection) adds a **reverse traceability check** to specback. While specback's main direction is **code → spec** (reverse engineering), Phase 7 answers: "*After source code changes, which spec sections need attention?*"

This is the **foundation** for future incremental update features (Phase 7b REF Auto-Fix, Phase 7c ChangeSpec).

## Problem

After specback generates a spec (Phase 6), the codebase keeps evolving. Without drift detection:

- Engineers must manually compare code changes against the spec
- REF line numbers become statically stale
- New source files fall outside spec coverage
- Deleted files leave orphaned REF markers
- The spec gradually diverges from reality, eventually becoming unreliable

## Design Goals

1. **Zero external dependencies** — stdlib only, no PyPI packages
2. **Leverage existing artifacts** — use `source-map.json` and `trace.json` as the single source of truth
3. **Git-native** — use `git diff` for change detection (fits existing workflows)
4. **CI-friendly** — accept piped diff input, output both Markdown and JSON
5. **Honest about limitations** — detect and report, but do **not** auto-fix (handled by Phase 7b)

## Dataflow

```
git diff --name-status HEAD
        │
        ▼
  ┌─ Changed files ────────────────────┐
  │  M app/models/issue.rb             │
  │  A app/services/bulk_export.rb     │
  │  D app/old/deleted.rb              │
  └────────────────────────────────────┘
        │
        ▼ Lookup in source-map.json
  ┌─ SRC-IDs ──────────────────────────┐
  │  SRC-0142 (app/models/issue.rb)   │
  │  SRC-0143 (app/models/issue.rb)   │
  │  (new file: no SRC-ID)            │
  │  SRC-0099 (app/old/deleted.rb)    │
  └────────────────────────────────────┘
        │
        ▼ Lookup in trace.json.by_source
  ┌─ Spec sections ────────────────────┐
  │  02-entities.md §2.1 Issue        │
  │  04-data.md §4.3 Issues table     │
  │  (deleted: 02-entities.md §2.2)   │
  └────────────────────────────────────┘
        │
        ▼ Impact classification
  ┌─ Drift report ─────────────────────┐
  │  drift-report.md (human-readable)  │
  │  drift-report.json (machine-reab.) │
  └────────────────────────────────────┘
```

## Impact Classification Heuristics

| Change type | Impact | Reason |
|------------|--------|--------|
| `A` (Add) | **high** | New source not registered in source-map. Spec coverage gap |
| `D` (Delete) + spec refs exist | **high** | REF markers point to a non-existent file |
| `M` (Modify) + spec refs exist | **moderate** | REF line numbers may be stale. Content may differ |
| `R` (Rename) + spec refs exist | **moderate** | REF marker file paths need updating |
| `T` (Type change) + spec refs exist | **moderate** | Base file type changed (e.g. symlink → regular) |
| `M` (Modify) + no spec refs | **low** | Changed but not referenced in any spec section |
| `C` (Copy) | **low** | New copy; doesn't break existing references |

## Output Schema (drift-report.json)

```json
{
  "schema_version": "0.1.0",
  "generated_at": "2026-07-28T12:00:00Z",
  "base": "HEAD",
  "summary": {
    "changed_files": 3,
    "affected_spec_sections": 2,
    "new_uncovered_sources": 1,
    "deleted_sources_with_refs": 0,
    "no_impact_changes": 1
  },
  "changes": [
    {
      "file": "app/models/issue.rb",
      "status": "M",
      "src_ids": ["SRC-0142", "SRC-0143"],
      "impacted_sections": [
        {"file": "02-entities.md", "section": "2.1 Issue", "impact": "moderate"},
        {"file": "04-data.md", "section": "4.3 Issues table", "impact": "moderate"}
      ]
    }
  ],
  "deleted_with_refs": [],
  "new_uncovered": [
    {"file": "app/services/bulk_export.rb", "status": "A",
     "reason": "New file not present in source-map.json"}
  ],
  "no_impact": [
    {"file": "config/initializers/newrelic.rb", "status": "M",
     "reason": "Not in source-map"}
  ]
}
```

### Git diff to source-map path matching

Rules for matching git diff file paths to source-map.json unit paths:

1. **Exact match** — `app/models/issue.rb` == `app/models/issue.rb`
2. **Suffix match** — `models/issue.rb` ends with `/app/models/issue.rb` (absorbs workspace-relative path differences)
3. **Filename match** — `issue.rb` matches via `endswith` (fallback)

#### Rename handling (`R` status)

`git diff --name-status` outputs rename entries as 3 tab-separated fields:

```
R100\told/path.rb\tnew/path.rb
```

- `file` is set to the **new path** for source-map / trace lookups
- `old_file` is kept separately and checked against trace.json via `_check_deleted_path_in_trace()` for orphaned REF detection
- If the old path has trace references, the rename is flagged as `deleted_with_refs` (high impact)
- If the old path has no trace coverage, it's listed under `no_impact` with reason "Renamed from..."
- `R` status impact: **moderate** (REF marker file paths need updating)

This uses the same matching heuristic as `build-trace.py`'s `resolve_refs_to_units()` for consistency.

## Edge Cases

| Case | Handling |
|------|----------|
| No changes | Empty report ("no changes") |
| `{output_dir}/.specback/` doesn't exist | Exit code 2 |
| `source-map.json` missing | Exit code 2 |
| `trace.json` missing | Exit code 2 |
| Not a Git repo | Use `--diff` flag to pass manual diff |
| Binary file changes | Skipped (no SRC-ID match, no impact) |
| File rename + content change | Shown as `R` in name-status. Treated as moderate impact |
| Large diff (500+ files) | Aggregate stats reported correctly (single pass, no pagination) |

## Phase 7b — Incremental Update (`scripts/fix-refs.py`)

`scripts/fix-refs.py` extends Phase 7 by auto-correcting `<!-- REF: ... -->` markers.

### Mechanism

1. Run `git diff -U0 <base>` to obtain hunk-level diffs
2. Parse each hunk header (`@@ -old,count +new,count @@`) to build per-file line-number mappings
3. Scan spec files in `{output_dir}/.specback/drafts/` or `{output_dir}/` (default: `{output_dir}/`) for `<!-- REF: ... -->` markers
4. For each marker referencing a changed file:
   - **Line preserved**: update line number to new position
   - **Line deleted**: flag as orphaned (manual review required)
   - **Range affected**: adjust start and end independently

### Usage

```bash
# Dry-run (default)
python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" --specback-dir {output_dir}/.specback

# Apply corrections
python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" --specback-dir {output_dir}/.specback --apply

# CI check: exit 1 if any orphaned REFs remain
python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" --specback-dir {output_dir}/.specback --check

# Pipe diff from CI
git diff -U0 main...HEAD | python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" --diff - --check
```

### Safety

- **Default is dry-run**: files are not modified until `--apply` is passed
- **Backups**: original files saved to `{output_dir}/.specback/backups/<file>.bak` before modification
- **Check mode**: ideal for CI gates — fails if orphans remain after correction
- **Conservative mapping**: only lines explicitly present within hunk ranges are modified. Lines outside ranges are not changed even if the range length appears to have shifted

## Implemented Extensions

- **Phase 7d — Config Refresh** (`skills/specback/phases/phase-7d-config-refresh.md`): after drift detection, regenerates `source-map.json`, `trace.json`, and updates `state.json.generated_at_commit` so subsequent drift sessions don't re-flag the same changes.

## Future Extensions

- **Spec section re-investigation**: run chapter investigation subagents only for affected sections
- **Pre-commit hook integration**: gate commits on drift status

## Related

- `scripts/detect-drift.py` — implementation
- `SKILL.md §Phase 7` — agent workflow definition
- `scripts/build-trace.py` — generates `trace.json` used by this phase
- `scripts/source-map.py` / `source_map_v2/` — generates source maps used by this phase
- `scripts/snapshot-hashes.py` — hash snapshot for non-Git projects (hash mode)
- `scripts/fix-refs.py` — Phase 7b: REF auto-correction
- `scripts/change-spec.py` — Phase 7c: ChangeSpec mechanical extraction
- `skills/specback/phases/phase-7d-config-refresh.md` — Phase 7d: Config Refresh procedure
