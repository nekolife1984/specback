## Phase 0: Setup & Goal

### Purpose
Right after the skill starts, fix the scope and the goal. Every later decision derives from the goal defined here.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:
1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**: `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"`, `TARGET_ROOT = scope.root`.
3. **Ensure `.skill-path`** (validate → re-resolve): `SP="$(cat {output_dir}/.specback/.skill-path 2>/dev/null)"; [ -z "$SP" ] || [ ! -d "$SP/scripts" ] && echo "$PWD" > {output_dir}/.specback/.skill-path; mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the procedure below** using `{SPECBACK_DIR}` and `{TARGET_ROOT}`.
5. **On completion**: Increment `goal.current_scope`. If `current_scope >= scopes.length`, reset to `0`.
6. **Resume support**: Save `state.json` with `current_scope` after each scope.
7. **At START**: If `goal.current_scope > 0` and `multi_scope == true`, skip completed scopes.

When `goal.multi_scope == false` (default), run once with `{output_dir}/.specback/` as the specback directory.

### Procedure

1. **Project confirmation**
   - Start from the current working directory and identify the target project.
   - Ask the user "Is this the right root directory for the target codebase?". If not, obtain the correct path.

1.5. **Auto-migration of legacy `.specback/` (one-time)**
   - If `project-root/.specback/` exists (the legacy location), this is an old project that needs migration:
     a. Read the existing `goal.json` from `.specback/` to get `output_dir` (or default to `"specs"`).
     b. Create the new dir: `mkdir -p "{output_dir}/.specback"`
     c. Copy all contents: `cp -a .specback/* "{output_dir}/.specback/"`
     d. Remove the old directory: `rm -rf .specback`
     e. If `goal.json.output_dir` was the legacy default `".specback"`, update it to `"specs"`.
     f. Report the migration to the user.
   - This is a one-time migration. After this, `.specback/` always lives at `{output_dir}/.specback/`.
   - In resume mode, the new location is used automatically.

2. **Initialize the state directory**
   - Determine the specback directory: `SPECBACK_DIR = "{output_dir}/.specback"`. Create it.
   - **Record the skill path**: every helper invocation in this document refers to scripts and references through a path derived from `{SPECBACK_DIR}/.skill-path`. Write the absolute path to the skill root (the directory containing `SKILL.md`) into `{SPECBACK_DIR}/.skill-path`:
     ```bash
     mkdir -p "{output_dir}/.specback"
     echo "/absolute/path/to/specback/skill/root" > "{output_dir}/.specback/.skill-path"
     ```
     **Replace `/absolute/path/to/repo/root`** with the real path. The skill reference files and shared utilities are at `references/`, `scripts/`, `schemas/`, etc. in the repo root.
   - **If an existing `{SPECBACK_DIR}/state.json` is found, branch to resume mode** (see "State management and resume" below). Resume mode **re-reads and validates** `.skill-path` before continuing (Issue #372 / SB-01):
     ```bash
     SP="$(cat "{output_dir}/.specback/.skill-path" 2>/dev/null)"
     if [ -z "$SP" ] || [ ! -d "$SP/scripts" ]; then
       # Recorded path is stale/absent — re-resolve to the currently running skill root
       # (the directory containing the SKILL.md you are reading right now).
       echo "$PWD" > "{output_dir}/.specback/.skill-path"
     fi
     ```
     This lets a moved repo / DevContainer / re-installed skill resume without manual edits. (The same resolve-then-fallback logic is shared in `scripts/common.py::resolve_skill_path` — used by `gates.py`.)

3. **Output language selection**

   - **This step alone is presented bilingually** because the user's preferred language has not yet been confirmed. The question body and choice labels appear in both English and Japanese.
   - Use `AskUserQuestion` with:
     - Question: `Select the output language for the dialogue and the generated specs / 対話と生成ドキュメントの出力言語を選択してください`
     - Choices (**fixed order; English is the default**):
       1. `English`
       2. `日本語 (Japanese)`
     - `allow_multiple = false`, `allow_free_text = false`
   - Map the selected label to `output_language`: `English` → `"en"`, `日本語 (Japanese)` → `"ja"`. Persistence to `goal.json` happens together with the other answers in Step 5.
   - **Default policy (English-base)**: when the user submits without changing the highlighted choice, treat the answer as `"en"`. This matches the specback upstream policy.
   - **Parent-harness hint precedence**: when the parent harness injects a `userUiLanguage` hint into the initial prompt, use that hint to decide which choice is **pre-highlighted** (`en` highlights `English`; `ja` highlights `日本語 (Japanese)`). The hint never overrides the user's explicit selection. Priority order:
     1. The user's explicit click in this step (highest)
     2. `userUiLanguage` hint passed from the parent harness's initial prompt
     3. Hard default `"en"` (lowest)
   - **All natural-language output from Step 4 onward** — `AskUserQuestion` bodies and choices, confirmation summaries, chapter titles, generated spec body, `questions.json` body text, etc. — is rendered in the language selected here (see Design Principle #11).
   - **Resume mode**: when `{output_dir}/.specback/goal.json` already exists, read the persisted `output_language` and skip this question entirely.

4. **Run the 6 goal-definition questions**
   - Use `AskUserQuestion` to ask the following 6 questions in sequence. **Question bodies, choice labels, and free-form-input placeholders are all rendered in the `output_language` selected in Step 3.** The choice labels below are shown when `output_language == "en"`; the agent dynamically translates them when `output_language == "ja"` (enum values such as `primary_reader: "maintenance_developer"` stay as language-independent English enums in `goal.json`). Each question is choice-based first with a free-form field as a fallback.
   - **Question-text quality contract (applies to every `AskUserQuestion` call in every phase, especially when translating into `output_language == "ja"`)**:
     1. **NEVER JSON-escape characters.** Emit raw UTF-8 only. If you find yourself writing `あ` or any other `\uXXXX` form inside the `question` or `choices` strings, that is a defect — decode it before emitting. A user who sees `次の中` on screen will reject the run.
     2. **Use only standard Japanese kanji.** Stay within JIS Level 1 / 常用漢字 / 人名用漢字. Do NOT mix in Chinese-simplified variants (e.g. `优 (Chinese)` ← write `優 (Japanese)`; `寸叧` is not a valid word — `対応` is). The runtime has no automatic fix for these; they reach the user verbatim.
     3. **Self-check before emit.** After translating a label to Japanese, mentally re-read it. If any kanji feels unusual for the surrounding context — e.g. `妊` (pregnancy) appearing in `業務妊当性` instead of `妥` (`妥当性` = validity) — regenerate the entire label. Common confusion pairs to double-check: 妥/妊, 暑/署, 復/複, 製/制, 即/則.
     4. **No invented characters / kanji.** If you are unsure of a kanji, use kana (e.g. write `たいおう` instead of `寸叧`). Hiragana is always safer than a wrong kanji.
     5. These rules apply to **`AskUserQuestion` bodies and choices**, but they do NOT relax the rule that JSON keys, enum values, file names, and machine-readable markers stay English (see Principle #11).

   **Q1. Who is the primary reader of the spec?**
   - Maintenance developer
   - Delivery customer
   - SME (subject-matter expert)
   - Regulator
   - Other (free-form)

   **Q2. What will the reader do after reading the spec?**
   - Code change
   - Approval decision
   - Audit
   - Learning
   - Other (free-form)

   **Q3. What level of granularity is preferred?**
   - High-level overview
   - Medium
   - Detailed
   - Other (free-form)

   **Q4. Which perspectives should be emphasised? (multi-select)**
   - Functional correctness
   - Business validity
   - Security
   - Operability
   - Performance
   - Other (free-form)

   **Q5. What about existing documentation?**
   - No existing docs
   - Existing docs / want to update
   - Existing docs / want to coexist
   - Existing docs / want to retire
   - Other (free-form)

   **Q6. Where should the spec documents be written?**
   - Default (specs)
   - Custom path (free-form, relative to project root)

   - Q6 specifies the **final spec output directory**. Default is `specs` → final spec files go to `specs/`. The config/state directory `.specback/` lives at `{output_dir}/.specback/`. When a custom path like `docs/specs` is given, final spec files go directly to `docs/specs/`, and `.specback/` is at `docs/specs/.specback/`. Drafts always stay at `{output_dir}/.specback/drafts/` regardless. State files (goal.json, state.json, trace.json, etc.) remain in `{output_dir}/.specback/`.
   - In resume mode, read `goal.json.output_dir` and skip this question.

5. **Extract `user_custom_deliverables` from `free_text_notes`**
   - **Mandatory.** Before persisting `goal.json`, scan `free_text_notes` for explicit deliverable filenames using the regex `\b[a-z][a-z0-9_-]*\.md\b` (case-insensitive). De-duplicate and exclude any name matching the chapter-naming regex `^(0\d|[1-9]\d)-[a-z0-9-]+\.md$` or the reserved names `00-metadata.md` / `99-unresolved.md` / `traceability.md` (those are handled by the standard chapter pipeline).
   - The remaining names are **user-promised custom deliverables**. They MUST appear in `{output_dir}/` at Phase 6 completion; missing any of them is a hard failure (check 12 in `coverage-check.py`).
   - Example: `free_text_notes = "顧客向けドキュメント。Mermaid図による視覚的説明と、紙芝居的な manual.md を含める。"` → `user_custom_deliverables = ["manual.md"]`.
   - If the free-form text is empty or contains no `*.md` references, the list is `[]`.
   - User-custom files are **exempt from comprehensive per-chapter quality gates** (the 200-lines / 10-REFs / Mermaid minimums) because their quality bar is the user's intent recorded in `free_text_notes`, not the source-derived spec-chapter bar. Only existence + non-empty body is enforced.

5.5 **Show what each selection changes (goal → spec reflection table), THEN confirm**

   - Before persisting (Step 6), present a **confirmation summary** that maps each of the 6 answers to how it actually affects the generated spec. The goal is transparency: the user should know which choices **deterministically** change the output vs. which only flow through as **agent context** (soft guidance). Use the following table (rendered in `output_language`; the "Field" / "Kind" columns stay English):

   | Q | goal.json field | Kind | What it changes |
   |---|-----------------|------|-----------------|
   | Q1 | `primary_reader` | 🔹 **Deterministic** | Phase 2 picks the template's `reader_order[primary_reader]` → chapter **ordering** (e.g. delivery_customer pulls installation/usage forward; regulator pulls constraints/design-decisions early). See `references/template-catalog.md` → Reader-adaptive ordering. |
   | Q2 | `reader_action` | ◻️ **Agent context** | Passed to the sub-agent prompt as "what the reader does after reading". Guides prose emphasis; no deterministic structural change. |
   | Q3 | `granularity` | ◻️ **Agent context** (soft) | Passed to the sub-agent prompt's "Granularity interpretation" (high-level / medium / detailed) → guides each chapter's level of detail. **Independent of `depth_mode`** (see note below). |
   | Q4 | `perspectives` | ◻️ **Agent context** | Passed to the sub-agent prompt as "emphasised perspectives"; guides which aspects get raised. No deterministic structural change. |
   | Q5 | `existing_docs` | ◻️ **Agent context** | Passed to the sub-agent prompt; guides how existing docs are positioned (update / coexist / retire). No deterministic structural change. |
   | Q6 | `output_dir` | 🔹 **Deterministic** | Sets the final spec output directory (and `.specback/` location). Direct filesystem effect. |

   - **Clarify the Q3 vs depth-mode distinction** when presenting: `granularity` (Q3) here is the **descriptive granularity within each chapter** (guided by the agent), while **`depth_mode`** (decided in Phase 1 from code scale — see `phase-1-recon.md` → "depth-mode & tone decision") is the **overall mode** (comprehensive vs. outline vs. interactive). The two are independent axes: a comprehensive spec can still be written at "high-level overview" granularity if the user asks, and an outline spec always uses table-first output regardless of granularity.
   - **Do not confuse this `granularity` with the template frontmatter `granularity`** (merge/split rules based on code volume, applied deterministically in Phase 1 — e.g. screens ≤ N → Screens/Routes merged, entities ≥ N → data-model split). That template-level `granularity` is a separate concept that IS a deterministic structural change; the `goal.json` field in this table is only the intended descriptive detail. Both share the name `granularity` but act in different places.
   - Present the table as part of the confirmation step (the same one referenced in Phase-specific cautions), so the user validates both **their answers** and **what each answer will change**. Confirm before persisting.

6. **Persist to `goal.json`**
   - Save the language choice from Step 3, the 6 answers from Step 4, and the `user_custom_deliverables` array from Step 5 as a structured `goal.json` under `{output_dir}/.specback/`. Schema:

   ```json
   {
     "output_language": "en",
     "output_dir": "specs",
     "primary_reader": "maintenance_developer",
     "reader_action": "code_change",
     "granularity": "medium",
     "perspectives": ["functional_correctness", "operational"],
     "existing_docs": "none",
     "free_text_notes": "...",
     "user_custom_deliverables": ["manual.md"],

     "multi_scope": false,
     "scopes": [],
     "current_scope": 0
   }
   ```
   - `output_language` is required and must be `"en"` or `"ja"`. Other enum fields (`primary_reader`, `reader_action`, `granularity`, `perspectives`, `existing_docs`) are language-independent English enums (localized only at display time using `output_language`).
   - `output_dir` specifies the final spec output directory. Default is `"specs"`. The config/state directory `.specback/` lives at `{output_dir}/.specback/`. Final spec files go to `{output_dir}/`. Drafts stay at `{output_dir}/.specback/drafts/`. State files remain in `{output_dir}/.specback/`.
   - `user_custom_deliverables` is a (possibly empty) array of file names that the user explicitly requested in `free_text_notes`. These bypass the chapter-naming regex; their filenames are preserved verbatim. Phase 2 adds them to `wbs.json` as `kind: "user_custom"` chapters; Phase 6 verifies every one of them exists in `{output_dir}/`.
   - 🆕 **`multi_scope`** (boolean, default `false`): set to `true` when the target is a monorepo and the user chooses to generate separate specs per system.
   - 🆕 **`scopes[]`** (array of objects, empty by default): each entry specifies `{"name": "...", "root": "..."}` where `name` is a short slug (e.g. `auth`) and `root` is the relative path to the system root (e.g. `services/auth`). Populated in Phase 1 when `multi_scope` becomes `true`.
   - 🆕 **`current_scope`** (integer, default `0`): index into `scopes[]` tracking which scope is currently being processed. Used for resume across multi-scope phases. When `scopes.length > 0` and all scopes have been processed, this resets to `0` before advancing to the next phase.

7. **Phase 0 complete**
   - Update `state.json` and proceed to Phase 1.

### Phase-specific cautions
- Minimise the user's burden by leading with choice-based UI; never force the user to type the same thing twice.
- Treat the free-form field as a "none of the above" safety net; it is unnecessary when the user picked one of the choices.
- The goal influences every later phase, so do not skip summarising the answers and asking the user to confirm. **The confirmation summary is also rendered in `output_language`.** As part of the confirmation, present the **goal → spec reflection table** (see Step 5.5) so the user sees not only their answers but also **what each choice deterministically changes** (chapter order for `primary_reader`, output location for `output_dir`) vs. **which choices are soft agent-context guidance** (`reader_action`, `granularity`, `perspectives`, `existing_docs`).
- The output-language selection (Step 3) is **bilingual only for that first dialogue**. From Step 4 on, use the confirmed language exclusively. If the user requests a language switch mid-flight, update `goal.json.output_language` and individually check whether existing `drafts/` and `questions.json` bodies need to be re-rendered.

---
