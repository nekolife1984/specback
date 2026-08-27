## Phase 7d: Config Refresh

### Purpose

After Phase 7 detects drift and Phase 7b/7c process the changes, the infrastructure files (`source-map.json`, `trace.json`, `state.json`) still reflect the **pre-change** state. Phase 7d refreshes these artifacts so subsequent drift detection sessions don't re-flag the same changes.

Without Phase 7d:
- New files stay unregistered in `source-map.json` (flagged as `new_uncovered` every time)
- Deleted files leave orphaned REFs in `trace.json`
- `state.json.generated_at_commit` remains at the old baseline

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, iterate over each scope and run the procedure for each:
1. Read `goal.scopes[]`.
2. For each scope, set `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` and run the procedure below.
3. Each scope regenerates its own source-map / trace.

When `goal.multi_scope == false` (default), run the procedure once with `{output_dir}/.specback/`.

### Prerequisites

- Phase 7 drift report must exist (`drift-report.md` / `drift-report.json`)
- Phase 7b (REF Auto-Fix) must have completed (optional but recommended — fresh REFs before rebuilding trace)
- Phase 7c (ChangeSpec) may or may not have run (Phase 7d is independent of 7c)
- `source-map.py` and `build-trace.py` scripts accessible (via `.skill-path`)

### Procedure

1. **Confirm with user** (AskUserQuestion):
   - "コード変更を source-map / trace に反映しますか？（Phase 7d: Config Refresh）"
   - Choices: Yes / No
   - If No, skip Phase 7d entirely.

2. **Regenerate source-map.json**:
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/source-map.py" \
     --target . --output-dir {output_dir}/.specback
   ```
   This captures new files and removes deleted files from the source map.

3. **Regenerate trace.json**:
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/build-trace.py" \
     --specback-dir {output_dir}/.specback
   ```
   This rebuilds the REF-to-SRC-ID cross-reference, eliminating orphaned entries from deleted files. The canonical `trace.json` is written to `{output_dir}/.specback/trace.json`; the scan dir defaults to `final`.

4. **Update state.json**:
   - Set `generated_at_commit` to the current HEAD:
     ```bash
     git rev-parse HEAD
     ```
   - Append to `session_history`:
     ```json
     {
       "event": "config_refresh: Phase 7d",
       "timestamp": "<ISO 8601>"
     }
     ```

5. **Regenerate source-hashes.json** (hash mode only):
   If the project uses hash mode (no Git repository):
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/snapshot-hashes.py" \
     --target . --output {output_dir}/.specback/source-hashes.json
   ```

6. **Report completion**:
   Present a summary of what was refreshed:
   - `source-map.json` — updated (N entries)
   - `trace.json` — regenerated (M sections)
   - `state.json.generated_at_commit` — set to `<hash>`
   - `source-hashes.json` — updated (if hash mode)

   > **Design rationale:** Phase 7d is a separate phase (not a `--refresh` flag) to keep the "detect only" contract of `detect-drift.py` intact. Running after REF Auto-Fix means `build-trace.py` already has corrected REF line numbers. Config Refresh is independent of ChangeSpec — skipping ChangeSpec should not block infrastructure maintenance.

   > **Output artifacts:**
   > - `{output_dir}/.specback/source-map.json` — updated
   > - `{output_dir}/.specback/trace.json` — regenerated
   > - `{output_dir}/.specback/state.json` — `generated_at_commit` updated
   > - `{output_dir}/.specback/source-hashes.json` — regenerated (hash mode only)

   > **Usage examples (manual invocation):**
   > ```bash
   > # Manual refresh (standalone, outside agent workflow)
   > python scripts/source-map.py --target . --output-dir {output_dir}/.specback
   > python scripts/build-trace.py --specback-dir {output_dir}/.specback
   > # Then update state.json manually
   > ```

### Phase-specific cautions
- Running Phase 7d without Phase 7b (REF Auto-Fix) means stale REFs remain in the regenerated `trace.json`. Run 7b first for best results.
- Regenerating `source-map.json` overwrites the previous version. The old version is not backed up automatically.
- Config Refresh resets the drift baseline. After Phase 7d, the next drift detection will start from a clean slate — inform the user.
- Multi-scope: each scope's infrastructure files are refreshed independently.

## References
