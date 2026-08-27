## Phase 3: Investigate (read code, then write chapters)

### Purpose
Based on the WBS, **read the real source code first, then write each chapter**.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:

1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**:
   - `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` (e.g. `docs/auth/.specback`)
   - `TARGET_ROOT = scope.root` (e.g. `services/auth`)
3. **Ensure `.skill-path`** (validate → re-resolve): `SP="$(cat {output_dir}/.specback/.skill-path 2>/dev/null)"; [ -z "$SP" ] || [ ! -d "$SP/scripts" ] && echo "$PWD" > {output_dir}/.specback/.skill-path; mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the phase procedure below** using `{SPECBACK_DIR}` as the specback directory and `{TARGET_ROOT}` as the target codebase root for source-map scanning.
5. **On completion**: Increment `goal.current_scope` in `{output_dir}/.specback/goal.json`. If `current_scope >= scopes.length`, reset to `0` (all scopes done for this phase).
6. **Resume support**: After each scope completes, save `state.json` with `current_scope` so the session can resume from the correct scope.
7. **At the START of this phase**: If `goal.current_scope > 0` and `goal.multi_scope == true`, this is a resume — skip already-completed scopes and start from `goal.current_scope`.

When `goal.multi_scope == false` (default), run the phase procedure once with `{output_dir}/.specback/` and the project root as before.

### 🆕 depth-mode branching (important)

`{output_dir}/.specback/goal.json`'s `depth_mode` **changes which detail file to load**:

| depth_mode | Detail file | Main behaviour | Chapter body shape |
|------------|-------------|----------------|-------------------|
| `comprehensive` | `phase-3a-comprehensive.md` | Apply STEP A-F to every chapter | Full prose per section. tone:concise -> compact; tone:thorough -> detailed |
| **`outline` / `interactive`** | `phase-3b-outline.md` | Table-first + relationship diagrams + deep-dive candidate list | Table-first + relationship diagrams + deep-dive candidate list |

**Execution rule**: Before starting Phase 3, read the corresponding detail file based on `goal.depth_mode`:
- `depth_mode == "comprehensive"` → read `phase-3a-comprehensive.md` first
- `depth_mode == "outline"` or `"interactive"` → read `phase-3b-outline.md` first

### Phase 3 progression gate (mandatory)

Do NOT declare Phase 3 complete unless **every** chapter in `wbs.json.chapters[]` (standard, reserved, AND user_custom) has a non-empty body in `{output_dir}/.specback/drafts/` (at least 10 non-blank lines outside of code fences). The agent MUST verify this before updating `state.json` to mark Phase 3 complete; declaring "complete" while chapters are still stubs is a contract violation and triggers an immediate Phase 4 fail.

### Phase-specific cautions (cross-mode)

- **Cross-chapter consistency** is checked in Phase 4.
- **Do not hide uncertainty markers**; keep them explicit in the draft. They are the starting point for Phase 5 dialogue.
- **Feature specifications chapter (Ch2)**: This chapter has a different code-reading strategy than other chapters. See `references/outline-tables.md` -> **Feature grouping patterns** for the feature extraction strategy. Unlike other chapters, feature-level info may have a higher 🔴 ASSUMED ratio — this is expected and acceptable. The Phase 4 gate for confidence ratio does not apply to this chapter (i.e. the 60% 🔴 ratio warning in `coverage-check.py` is informational only for Ch2).
- **Module architecture (overview) chapter (Ch3 in library-sdk, Ch3 in web-app/api-service/batch-system as Architecture overview)**: overview-level only — module composition (directory structure), top-level dependency graph (import analysis), and tech stack (manifest). Keep it short and skimmable; defer detailed module internals to the Internal structure chapter and deep rationale to Design decisions (see `references/outline-tables.md` -> **Module architecture (overview) extraction patterns**). Confidence is typically 🟢 because directories, manifests, and import statements are mechanically extractable.
- **Design decisions chapter (last detailed chapter)**: This chapter uses import analysis and cross-cutting pattern detection rather than per-file deep reading. See `references/outline-tables.md` -> **Design decisions extraction patterns** for the extraction strategy. The ADR section may have many 🔴 ASSUMED entries (design rationale is rarely explicit in code) — this is expected and acceptable.
- **内部ファイル非表示ルール**: 生成ドキュメント本文内で `{output_dir}/.specback/inventory.json`、`{output_dir}/.specback/wbs.json` などの specback 内部ファイルパスを参照しないこと。テーブル列の説明はユーザー向け表現（例: 「該当機能を実装するソースコードの単位を示す」）にし、内部ファイル名で説明しない。Agent が内部で参照するファイルと、読者に表示する内容は別物である。
