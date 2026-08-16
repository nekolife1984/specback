## Phase 3b: Outline Investigation

### Purpose

Table-first chapters with `outline` / `interactive` depth mode — generates Layer 1 (exhaustive tables) and Layer 2 (relationship diagrams). Details produced on-demand via Phase 6.5 deep-dives.

### Procedure (outline / interactive mode)

#### OUT-A: Generate Layer 1 chapters (02-entities / 03-actions / 04-data / 05-dependencies)

Each Layer 1 chapter **exhaustively lists the "overview table" for that language**. Procedure:

1. Consult `$(cat .specback/.skill-path)/references/outline-tables.md` for the per-language catalogue.
2. **Use `glob` + `grep` to mechanically extract every entity**:
   - Ruby/Rails models: `grep "^class \\w+" --type ruby app/models/`
   - Controllers: `grep "^class \\w+Controller" --type ruby app/controllers/`
   - Etc., using the patterns from outline-tables.md for the target language.
3. Render the result as an **exhaustive Markdown table** — no omissions. 1 entity = 1 row.
4. Always add a **Confidence label** in each cell (🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED):
   - 🟢: the file of that entity was confirmed by reading it with the Read tool
   - 🟡: only the `grep` hit was confirmed; body unread
   - 🔴: inference based on framework-typical behaviour
5. The summary column is 1 line (<= 80 characters). **Do not write detailed logic** — leave that to Layer 3 deep-dives.

**At the end of each chapter you MUST place a "deep-dive candidates" section** (see OUT-C).

#### OUT-B: Generate Layer 2 chapter (06-diagrams) — Mermaid

- ER diagram (auto-derived from Entities + Data tables)
- Module dependency diagram
- Representative sequence (1-3 of the most typical request flows)
- State-transition diagram (when key entities have `status` columns, etc.)

Each diagram has a **one-line caption** and a "how to read this" hint. If a diagram cell is `[INFERRED]`, say so explicitly.

#### OUT-C: "Deep-dive candidates" list at the end of each Layer 1 chapter

Place at the end of each chapter, using this format:

```markdown
### Deep-dive candidates (refer to them by ID)

- **D-001**: M-013 `Issue` class — authorisation guard logic [🔴 ASSUMED, complex]
- **D-002**: C-018 `ProjectsController#index` — visibility decision [🟡 INFERRED, business-critical]
- **D-003**: Sequence "Issue notification delivery" — subscribers resolution [🔴 ASSUMED]
```

Selection criteria (see the end of references/outline-tables.md):
1. Rows with many 🔴 ASSUMED labels.
2. High-complexity rows (top 10% by method count / association count / file line count).
3. Rows containing business-critical keywords (auth / payment / permission / audit, etc.).

#### OUT-D: Drop the body-length constraints

In outline mode:
- **The "10 REFs / 3 code blocks / 1 Mermaid" requirements do NOT apply.**
- Instead the MECE criterion is "**every entity appears in some row of some table**" (Phase 4's `coverage-check.py` decides this automatically).
- The chapter body consists of: table + 1-2 paragraphs of explanation + Mermaid diagrams (where applicable) + the deep-dive candidates list.

### Phase-specific cautions
- "Exhaustive entity listing" takes precedence. Apply Confidence labels honestly per cell — do NOT over-apply 🟢 (only for files actually viewed).
- Do NOT declare Phase 3 complete unless **every** chapter in `wbs.json.chapters[]` has a non-empty body in `{output_dir}/.specback/drafts/` (at least 10 non-blank lines outside of code fences). Verify before updating `state.json`.
- 内部ファイル非表示ルール: 生成ドキュメント本文内で `{output_dir}/.specback/inventory.json`、`{output_dir}/.specback/wbs.json` などの内部ファイルパスを参照しないこと。
