## Phase 7b: REF Auto-Fix

### Purpose

Auto-correct `<!-- REF: path:line -->` markers in spec files that have become stale due to source code changes. Run `scripts/fix-refs.py` to parse `git diff -U0` hunk headers and update line numbers.

**SRC-ID refs** (`<!-- REF: SRC-NNNN -->`) are **auto-skipped** by fix-refs.py — they reference source-map.json unit IDs rather than line numbers, so they remain stable across code changes. Simply regenerate the source-map after refactoring and all SRC-ID refs stay valid.

### SRC-ID migration (`--migrate-srcid`)

Convert existing `<!-- REF: path:line -->` markers to the stable SRC-ID form. Only **safe conversions** are performed: a REF is migrated only when its path + line range **exactly match** a unit's `line_range` in `source-map.json`. Anything else is reported and left as `path:line` (converting it would make the click-to-source position inaccurate).

```bash
# Dry-run: show what would be migrated and what cannot be
python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" \
  --specback-dir {output_dir}/.specback \
  --output-dir {output_dir} \
  --migrate-srcid

# Apply: rewrite exact-match REFs to <!-- REF: SRC-NNNN -->
python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" \
  --specback-dir {output_dir}/.specback \
  --output-dir {output_dir} \
  --migrate-srcid --apply
```

**REF form selection rule** (decide per REF):

| Condition | Form | Why |
|-----------|------|-----|
| Path exists in `source-map.json` AND range == a unit's `line_range` | `<!-- REF: SRC-NNNN -->` | Stable across refactors |
| File not in `source-map.json` (README, configs, scripts, tests, docs…) | `<!-- REF: path:line -->` | No unit to reference |
| Range covers imports/docstrings/multiple units | `<!-- REF: path:line -->` | Converting would shift click position |
| Partial overlap with a unit | `<!-- REF: path:line -->` | Inaccurate if converted |

The migration report lists every not-migratable REF with its reason (`file not in source-map`, `partial unit overlap`, `range mismatch`), so remaining `path:line` refs are a deliberate, reviewable decision — not an omission.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, iterate over each scope and run the procedure for each:
1. Read `goal.scopes[]`.
2. For each scope, set `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` and run the procedure below.
3. The `--output-dir` should include the scope name: `{output_dir}/{scope.name}` or the combined output.

When `goal.multi_scope == false` (default), run the procedure once with `{output_dir}/.specback/`.

### Procedure

1. **Run fix-refs.py** (default: dry-run)
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" \
     --specback-dir {output_dir}/.specback \
     --output-dir {output_dir}
   ```

2. **Review the proposed changes**

3. **Apply corrections**
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" \
     --specback-dir {output_dir}/.specback \
     --output-dir {output_dir} \
     --apply
   ```

4. **CI check mode**
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/fix-refs.py" \
     --specback-dir {output_dir}/.specback \
     --output-dir {output_dir} \
     --check
   ```

### Safety

- **Dry-run by default**: no files are modified until `--apply` is passed
- **Backups**: originals saved to `{output_dir}/.specback/backups/<file>.<timestamp>.bak` (timestamped — repeated applies never overwrite an earlier backup)
- **Position-based replacement**: each REF is rewritten at the exact line/column recorded by the scanner, not at the first occurrence of the same text elsewhere in the file (code examples or duplicate markers are never mis-targeted)
- **Symlink refusal**: spec files that are symlinks are skipped with an error (no writes outside the spec directory)
- **Check mode**: exits with code 1 if orphaned REFs remain after correction
- **Flag validation**: `--migrate-srcid` cannot be combined with `--diff`/`--base` (they are ignored by migration mode — the combination is rejected); `--check` has no effect in migration mode and prints a warning

### Snapshot management (hash mode)

For non-Git projects, generate a hash snapshot after Phase 6 completes:

```bash
python "$(cat {output_dir}/.specback/.skill-path)/scripts/snapshot-hashes.py" --specback-dir {output_dir}/.specback
```

### Phase-specific cautions
- Dry-run by default: review proposed changes before applying with `--apply`.
- Backups are saved to `{output_dir}/.specback/backups/<file>.<timestamp>.bak` — verify they exist before applying. The timestamp suffix means repeated applies never destroy the first backup.
- REF corrections shift line numbers in the spec files. After applying, re-run `coverage-check.py` to verify structural integrity.
- Multi-scope: run per scope — shared `.skill-path` but separate `SPECBACK_DIR`.

---
