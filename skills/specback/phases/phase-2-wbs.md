## Phase 2: Plan & WBS

### Purpose
Finalise the skeleton of the spec, decompose the work to fill each chapter into a WBS of sub-tasks, and extract the inventory into `inventory.json`.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:

1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**:
   - `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` (e.g. `docs/auth/.specback`)
   - `TARGET_ROOT = scope.root` (e.g. `services/auth`)
   - `OUTPUT_DIR = "{output_dir}/{scope.name}"` (e.g. `docs/auth`)
3. **Ensure `.skill-path`** (validate → re-resolve): `SP="$(cat {output_dir}/.specback/.skill-path 2>/dev/null)"; [ -z "$SP" ] || [ ! -d "$SP/scripts" ] && echo "$PWD" > {output_dir}/.specback/.skill-path; mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the phase procedure below** using `{SPECBACK_DIR}` as the specback directory (for scripts: `--specback-dir {SPECBACK_DIR}`) and `{TARGET_ROOT}` as the target codebase root (for source-map: `--target {TARGET_ROOT}`).
5. **On completion**: Increment `goal.current_scope` in `{output_dir}/.specback/goal.json`. If `current_scope >= scopes.length`, reset to `0` (all scopes done for this phase).
6. **Resume support**: After each scope completes, save `state.json` with `current_scope` so the session can resume from the correct scope.
7. **At the START of this phase**: If `goal.current_scope > 0` and `goal.multi_scope == true`, this is a resume — skip already-completed scopes and start from `goal.current_scope`.

When `goal.multi_scope == false` (default), run the phase procedure once with `{output_dir}/.specback/` and the project root as before.

---


### Procedure

1. **Build the effective chapter list from detection results, then generate skeletons**

   - **Read `goal.json.customized_chapters`** (Phase 1 detection result). This is the source of truth for which chapters to include:
     - `status == "included"` → include as-is
     - `status == "excluded"` → **skip entirely** (do NOT generate skeleton, do NOT assign a number)
     - `status == "auto_added"` → include at the `insert_after` position (look up the template's `detection_rules.extra_chapters[]` for the anchor)
     - `status == "merged"` → combine N chapters into one (use the merge rule's `into_title` from the template frontmatter)
     - `status == "split"` → expand one chapter into N sub-chapters (use the split rule's `into[]` from the template frontmatter)
   - **Assign sequential numbers** based on this effective list, starting at `01`, preserving the reader_order if set. **Do NOT derive numbers from the raw template slug list** — that would leave gaps at excluded chapter positions. Example: if `ch-auth` (slug `08-authentication-authorisation`) is excluded, the slug `08-authentication-authorisation` is never used; the subsequent chapter gets the next sequential number.
   - For merged chapters: generate ONE skeleton with the combined `into_title`. The meta comment mentions both source chapters (e.g. `<!-- meta: Screens (Ch5) + Routes (Ch6) combined -->`).
   - For split chapters: generate N skeletons, one per `into[]` entry. Each keeps an `original_chapter` reference in the meta comment.
   - For auto_added chapters: insert at the position AFTER the `insert_after` anchor. Adjust all subsequent chapter numbers accordingly.
   - **If `goal.json.customized_chapters` is absent** (no detection was run), fall back to the full template chapter outline (existing behaviour).

   - **Legacy rules** (apply after the effective list is built):
     - Every chapter file falls into one of three kinds; free naming by Claude is forbidden.
     - **Standard chapter** (`kind: "standard"`): `{NN}-{slug}.md`
       - `NN`: zero-padded two-digit chapter number (`00`-`99`)
       - `slug`: ASCII lowercase + digits + hyphens only (e.g. `01-overview.md`, `04-oauth-oidc.md`)
       - Strict regex: `^(0\d|[1-9]\d)-[a-z0-9-]+\.md$`
     - **Reserved chapter** (`kind: "reserved"`): one of `00-metadata.md` / `99-unresolved.md` / `traceability.md`.
     - **User-custom chapter** (`kind: "user_custom"`): every file name listed in `goal.json.user_custom_deliverables` (e.g. `manual.md`, `quickstart.md`). The relaxed regex `^[a-z][a-z0-9_-]*\.md$` applies; the user-provided file name is preserved verbatim.
     - **Chapter title in body**: handled independently of the file name. Rendered in `goal.json.output_language` (EN example: `# Chapter 1: Overview` / JA example: `# 第1章: 概要`).
     - **Chapter numbers are assigned by the main agent in Phase 2** and fixed in `wbs.json.chapters[].file_name`. Sub-agents never decide naming; they save under the file name handed down by the main agent.
   - **Reader-adaptive chapter ordering**: each template defines a `reader_order` frontmatter mapping `primary_reader` types to ordered chapter slug lists (see `references/template-catalog.md` → Reader-adaptive ordering). When assigning chapter numbers in Phase 2, read the template frontmatter, look up `reader_order[goal.json.primary_reader]`, and derive file names from that order. If the reader type is not listed (or `reader_order` is `null`), fall back to the template's default chapter outline order.
   - **Reserved numbers / file names** (must always be generated):
     - `00-metadata.md` (metadata chapter)
     - `99-unresolved.md` (unresolved-items chapter)
     - `traceability.md` (traceability table, no chapter number)
   - Regular chapter numbers are assigned sequentially in `01`-`98` while avoiding collisions with reserved numbers.
   - **When to generate them**: at Phase 2, create empty chapter files under `{output_dir}/.specback/drafts/` for all chapters — standard, reserved, AND user-custom — so every chapter has a skeleton to fill (the body is filled in Phase 3 / Phase 5 / Phase 6 depending on `kind`).
   - Place a meta comment (`<!-- meta: ... -->`) at the top of each chapter file describing what that chapter covers.
   - The skeleton of `00-metadata.md` carries a meta comment indicating "Phase 6 will write goal.json snapshot / generation timestamp / commit hash / template selection result here".
   - The skeleton of `99-unresolved.md` carries a meta comment indicating "Phase 6 will aggregate `abandoned` entries from `questions.json` here".
   - The skeleton of `traceability.md` carries a meta comment indicating "Phase 6 will write the chapter/section → source mapping table here".
   - Every user-custom skeleton carries a meta comment indicating "Phase 3/5 will fill this chapter per the user's intent recorded in `goal.json.free_text_notes`; Phase 6 verifies it exists in `final/` via check 12 (existence + non-empty body)."

   #### Skeleton content contract (strict)

   The Phase 2 skeleton is **deliberately near-empty**. Every chapter draft file created in Phase 2 must contain **exactly** the following, and **nothing else**:

   1. The `<!-- meta: ... -->` comment line described above.
   2. One blank line.
   3. The chapter title `#` heading, rendered in `goal.json.output_language`.
   4. (Optional, for `standard` chapters only) a placeholder line `(to be filled in Phase 3)` at the end — Phase 3 will populate the body with `<!-- REF: ... -->` citations.

   Total body length per skeleton MUST be **≤ 5 non-blank lines** outside of code fences. This cap is the structural enforcement of "Phase 2 ≠ Phase 3".

   **Forbidden in Phase 2 skeletons** (writing any of these is a contract violation and rolls back the file):

   - ❌ Entity / module / route / endpoint tables (those are Phase 3 STEP A-C outputs based on real `glob`/`grep`/`view` reads of the codebase).
   - ❌ `<!-- REF: ... -->` citations (Phase 3 STEP B).
   - ❌ Mermaid diagrams (Phase 3 outline-mode OUT-B for `06-diagrams.md`).
   - ❌ Confidence labels (🟢/🟡/🔴) — these belong to populated tables, not skeletons.
   - ❌ Prose explaining "what this module / class does" — that's Phase 3's job after `view`-ing the file.
   - ❌ Cross-references like "see Chapter 5" before Phase 3 has actually decided Chapter 5's content.
   - ❌ Any sentence written from training-data knowledge of the framework / library / project. Phase 2 has NOT read the code yet; anything written here would be guessed, not grounded.

   **Why this restriction exists**: previous runs had Phase 2 writing 300+ line "skeletons" filled with entity tables (Confidence 🟡 = "grep hit unread"), generated from the model's prior knowledge of Rails / Django / etc., not from the actual repository. Phase 3 then either rubber-stamps that unverified content into `final/`, or re-does the work redundantly. The fix is to make the skeleton structurally too small to hold body content — if you find yourself wanting to write a table or a `<!-- REF: ... -->` citation, you are no longer building a skeleton, you are doing Phase 3 work, **STOP**.

   **Example of a correct skeleton** (standard chapter; the title heading and placeholder text are rendered in `output_language` — EN shown here, JA variant shown after):

   ```markdown
   <!-- meta: Entities table - exhaustive listing of models/structs/types/classes/interfaces -->

   # Chapter 2: Entities

   (to be filled in Phase 3)
   ```

   JA equivalent (when `output_language == "ja"`):

   ```markdown
   <!-- meta: Entities table - exhaustive listing of models/structs/types/classes/interfaces -->

   # 第2章: エンティティ

   (Phase 3 で記入予定)
   ```

   Note that the meta comment stays English in BOTH variants (it is a structural marker the chapter pipeline matches on). Only the chapter title (`# Chapter 2: Entities` / `# 第2章: エンティティ`) and the placeholder phrase switch by `output_language`.

   That is the entire file. No table. No entity names. No `belongs_to` notes. No diagrams. Phase 3 fills the rest after reading the real source.

   **Example of a violating skeleton** (do NOT do this in Phase 2 — EN shown for illustration):

   ```markdown
   <!-- meta: Entities table - ... -->

   # Chapter 2: Entities

   ## 2.1 Core entities

   | Entity | File | Description | Status |
   |---|---|---|---|
   | `Issue` | `app/models/issue.rb` | A tracked unit of work | 🟡 |
   | `Project` | `app/models/project.rb` | Container for issues | 🟡 |
   ...
   ```

   Even though the table looks plausible, it was written without reading `issue.rb` — the 🟡 label means "grep hit only, body unread", which Phase 2 has no business claiming. Save this for Phase 3 where the table cells get grounded in actual `view` output.

2. **Create the WBS**
   - Define sub-tasks that fill each chapter. The model is "1 sub-task = 1 sub-agent".
   - Sub-task granularity: split each sub-task to "a size that preserves accuracy". Too big → coarse output; too small → overhead.
   - **Include every user-custom deliverable** from `goal.json.user_custom_deliverables` as a `chapters[]` entry with `kind: "user_custom"`. These chapters share the existence/non-empty gate (check 12) but are exempt from comprehensive per-chapter gates (200 lines / 10 REFs / Mermaid); their quality bar is defined by the user's intent (`source_intent`) confirmed via Phase 5 dialogue.
   - Save the WBS to `wbs.json`. Schema:

   ```json
   {
     "chapters": [
       {
         "chapter_id": "ch-01-overview",
         "chapter_title": "Chapter 1: Overview",
         "file_name": "01-overview.md",
         "kind": "standard",
         "assigned_inventory_ids": ["INV-001", "INV-002"],
         "status": "pending"
       },
       {
         "chapter_id": "ch-manual",
         "chapter_title": "Customer Manual",
         "file_name": "manual.md",
         "kind": "user_custom",
         "source_intent": "顧客向けドキュメント。Mermaid図による視覚的説明と、紙芝居的なmanual.mdを含める。",
         "assigned_inventory_ids": [],
         "status": "pending"
       }
     ]
   }
   ```
   <!-- chapter_title example: EN "Chapter 1: Overview" / JA "第1章: 概要" — chosen by output_language -->

   - `file_name` is required; for `kind: "standard"` it must match `^(0\d|[1-9]\d)-[a-z0-9-]+\.md$`; for `kind: "reserved"` it must be one of the three reserved names; for `kind: "user_custom"` it must match `^[a-z][a-z0-9_-]*\.md$` AND appear in `goal.json.user_custom_deliverables`.
   - The three files `00-metadata.md` / `99-unresolved.md` / `traceability.md` appear with `kind: "reserved"` and an empty `assigned_inventory_ids` array; Phase 6 fills their bodies.
   - `source_intent` (user-custom only) verbatim-quotes the snippet of `free_text_notes` that established the deliverable, so Phase 3/5 has the user's words at hand.

3. **Inventory extraction (v3: source-map.py + mechanical conversion)**

   **STEP A (required)**: Run `scripts/source-map.py` (v1, original) OR `source_map_v2` (v2, role-typed) to extract source units automatically:
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/source-map.py" \
     --target <target root> \
     --output {output_dir}/.specback/source-map.json
   ```
   With v2:
   ```bash
   python -m source_map_v2 \
     --target <target root> \
     --output {output_dir}/.specback/source-map.json
   ```
   Either produces `source-map.json` with `SRC-NNNN` units — v2 adds role typing (`role`, `table`, `framework`) but the conversion below handles both.

   **STEP B (required)**: Run `scripts/build-inventory-from-sourcemap.py` to mechanically convert `source-map.json` → `inventory.json`:
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/build-inventory-from-sourcemap.py" \
     --source-map {output_dir}/.specback/source-map.json \
     --output {output_dir}/.specback/inventory.json
   ```
   This maps:
   - `source-map[].role` → `inventory[].type` (via built-in role→type table; overridable via `--role-to-type`)
   - `source-map[].name` → `inventory[].name`
   - `source-map[].path` → `inventory[].file`
   - `source-map[].line_range[0]` → `inventory[].line`
   - `source-map[].id` → `inventory[].related_source_ids[0]`
   - `inventory[].covered_by` → `[]` (filled by Phase 3)

   **STEP C**: Validate minimum INV count:
   ```
   inventory.json minimum count = max(50, files_scanned // 20)
   ```
   Falling below this fails `coverage-check.py`.

   **STEP D (optional, only when v1 source-map.py was used)**: For `source-map.json` v0.1.0 units that lack a `role` field, `build-inventory-from-sourcemap.py` falls back to `kind` as the inventory `type`. If a more precise role→type mapping is needed, pass `--role-to-type`:
   ```bash
   python build-inventory-from-sourcemap.py \
     --source-map {output_dir}/.specback/source-map.json \
     --output {output_dir}/.specback/inventory.json \
     --role-to-type '{"endpoint":"api","model":"entity"}'
   ```
   When using v2 (`source_map_v2`), roles are already assigned and the default mapping works out of the box.

   **Why this matters**: Before this script, Phase 2 had to manually group `source-map.json` units into conceptual units per `references/inventory-units.md` — a hand-written process that broke the Phase 2 skeleton content contract (Phase 2 was writing body content instead of skeletons). Now the inventory is *mechanically derived* from the source map, eliminating the manual grouping step and enforcing separation of concerns: Phase 1 extracts, Phase 2 converts, Phase 3 fills.

   macro/group/module style types in `inventory[].type` are forbidden. Always 1 class / 1 module / 1 action per row.

   Save the result to `inventory.json`. Schema:

   ```json
   {
     "units": [
       {
         "id": "INV-001",
         "type": "controller",
         "name": "IssuesController",
         "file": "app/controllers/issues_controller.rb",
         "line": 20,
         "covered_by": [],
         "related_source_ids": ["SRC-0142", "SRC-0143"]
       }
     ]
   }
   ```

   `related_source_ids` links to `source-map.json` units; this enables the MECE check in Phase 4.

4. **Map WBS chapters to inventory items**
   - For each inventory item, decide which chapter covers it in the WBS.

5. **🆕 Adjust the chapter structure based on depth mode (the mode confirmed in Phase 1.5)**

   Branch the WBS chapter structure on `.specback/goal.json`'s `depth_mode`:

   **(a) `comprehensive` (classic / audit use)**
   - Distribute `assigned_inventory_ids` across the selected template's chapter outline (see `templates/<selected>.md`; the chapter count is template-specific and must never be hardcoded).
   - Phase 3 generates ≥ 200 lines / ≥ 10 REFs per chapter.
   - **For large repos this takes hours to days. Assumes an audit reader.**

   **(b) `outline` or `interactive` (recommended default)**
   - **Chapters are restructured to "table-first"**. Use `references/outline-tables.md` to decide per language.
   - **Required chapters (Layer 1: MECE-guaranteed)**:
     - `01-modules-overview.md` — Modules table (exhaustive responsibility partitioning)
     - `02-entities.md` — Entities table (exhaustive listing of models / structs / types / classes / interfaces)
     - `03-actions.md` — Actions table (exhaustive listing of controllers / handlers / endpoints)
     - `04-data.md` — Data table (DB schema / migrations)
     - `05-dependencies.md` — Dependencies table (gem / pip / npm, etc.)
   - **Required chapters (Layer 2: relationship visualisation)**:
     - `06-diagrams.md` — Mermaid (ER diagram, module dependency, representative sequences, state transitions)
   - **Optional chapters (as needed)**:
     - `07-flows.md` — Per-use-case sequences (multiple)
     - `08-cross-cutting.md` — Cross-cutting concerns: auth / logging / transactions, etc.
   - **Reserved chapters stay** as `00-metadata.md` / `99-unresolved.md` / `traceability.md`.
   - **Do NOT enforce the 200-line body / 10-REF requirements**. Instead, `coverage-check.py` checks that **each table enumerates every entity**.
   - Record `depth_mode` on each chapter in `wbs.json` so Phase 3 / 4 can branch on it.

6. **User review**
   - Display the WBS and the skeleton and ask the user "Is it OK to start Phase 3 with this decomposition?".
   - In outline / interactive mode, also call out that the chapters are "overview-table-first" and obtain consent.

6.5. **🆕 Token estimate & budget gate (before Phase 3)**
   - Run `python scripts/specback-estimate.py --specback-dir {output_dir}/.specback` and show the **estimated token consumption** for Phase 3 to the user.
   - If the estimate exceeds the user's budget (optional `--budget-limit <tokens>`), the script warns / exits with code 2:
     - Suggest switching `depth_mode` to `outline` (see `goal.json`) and re-running — outline uses roughly half the tokens of comprehensive.
   - This step exists so the user can decide **before** Phase 3's parallel sub-agent investigation starts (token cost grows with codebase size and cannot be stopped mid-run).

7. **Phase 2 complete**
   - Update `state.json` and proceed to Phase 3.

### Phase-specific cautions
- **Excluded chapters (from Phase 1 detection) must NOT generate skeletons** — `goal.json.customized_chapters[].status == "excluded"` means skip entirely. Do not create a file, do not assign a number.
- **Merged chapters**: generate exactly ONE skeleton with `into_title`. The meta comment must list all original chapter IDs so Phase 3 knows which inventory items to cover.
- **Split chapters**: generate N skeletons. Number them sequentially (the parent's number becomes the first child; subsequent children get the next numbers). The meta comment includes `original_chapter: <id>`.
- Inventory extraction scripts are generated by Claude on the fly. Pre-built generic scripts cannot keep up with language-specific details.
- WBS granularity directly drives sub-agent precision. When in doubt, split finer.
- Skipping the user review causes large rework in Phase 3.
- **Strictly observe the chapter file naming convention**. Free-form names like `chapter2_architecture.md` or `第3章_認証.md` are NOT allowed. Violations are flagged by `scripts/coverage-check.py`.
- **Skeleton size cap (mandatory)**: every file under `{output_dir}/.specback/drafts/` produced in Phase 2 has **≤ 5 non-blank lines** of body outside code fences. Verify this immediately after writing each skeleton (`wc -l {output_dir}/.specback/drafts/*.md` for a sanity check); a skeleton that is already long has body content that belongs in Phase 3 — delete the body and keep only meta comment + title.
- **Phase 2 does NOT read code**: the only allowed source reads in Phase 2 are (a) for inventory extraction via `source-map.py`, (b) for deciding the depth_mode chapter structure. Reading individual class / model / controller files to write their description is **Phase 3's job**, not Phase 2's. If you catch yourself opening `app/models/issue.rb` to write what `Issue` does, you've crossed into Phase 3 — stop and finish Phase 2 first.

---
