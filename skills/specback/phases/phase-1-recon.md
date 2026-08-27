## Phase 1: Recon & Template

### Purpose
Get a rough mental model of the codebase via a shallow reconnaissance, then pick an appropriate spec template. At the end of Phase 1, register the high-level questions into the Question Bank.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:
1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**: `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"`, `TARGET_ROOT = scope.root`.
3. **Ensure `.skill-path`** (validate → re-resolve): `SP="$(cat {output_dir}/.specback/.skill-path 2>/dev/null)"; [ -z "$SP" ] || [ ! -d "$SP/scripts" ] && echo "$PWD" > {output_dir}/.specback/.skill-path; mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the procedure below** using `{SPECBACK_DIR}` and `{TARGET_ROOT}`.
5. **On completion**: Increment `goal.current_scope`. If `current_scope >= scopes.length`, reset to `0`.
6. **Resume support**: Save `state.json` with `current_scope` after each scope.
7. **At START**: If `goal.current_scope > 0` and `multi_scope == true`, skip completed scopes.

When `goal.multi_scope == false` (default), run once with `{output_dir}/.specback/` as before.

### Procedure

1. **Run the shallow reconnaissance**
   Read the following and summarise them in `recon-report.md`:
   - File tree structure (limited to depth 3-4, noise excluded)
   - Package-manager files (`package.json`, `composer.json`, `requirements.txt`, `pom.xml`, `build.gradle`, etc.)
   - Entry-point candidates (`main` functions, `index` files, routing definitions, etc.)
   - Existing documentation (`README.md`, `docs/`, `wiki`, etc.)
   - Build/deploy configuration (`Dockerfile`, `Makefile`, CI configs, etc.)
   - Language mix and estimated line counts
   - 🆕 **Tree-sitter availability**: Check whether `tree-sitter` is installed (`python -c "import tree_sitter"`). Record as `tree_sitter_available: true/false` in recon-report.md. When unavailable, note that some language extractors will fall back to file-level granularity.

2. **Present template candidates**
   - Consult `templates/catalog.json` (machine-readable registry) and `references/template-catalog.md` (human-readable guide) and propose candidates suitable for the target codebase.
   - `templates/catalog.json` is the canonical machine-readable source: each entry lists `name`, `description`, `chapters`, `detection_rules`, and `languages`. Use it to shortlist templates whose target languages overlap the codebase's detected language mix.
   - Use `AskUserQuestion` to present the candidates to the user.

   **Example template choices**:
   - I have my own template (specify path)
   - Web application spec (`templates/web-app.md`)
   - Batch processing system spec (`templates/batch-system.md`)
   - API service spec (`templates/api-service.md`)
   - Library/SDK spec (`templates/library-sdk.md`)
   - CLI tool spec (`templates/cli-tool.md`)
   - Infrastructure spec (`templates/infrastructure.md`)
   - Mobile app spec (`templates/mobile-app.md`)
   - Desktop app spec (`templates/desktop-app.md`)
   - Event-driven / Streaming spec (`templates/event-driven.md`)
   - Use whichever the agent recommends from reconnaissance

3. **Run code analysis for chapter customisation (🆕 detection-driven)**  
   After the template is selected, analyse the codebase to customise the chapter list before presenting it to the user.

   #### 3a. Read detection rules from the selected template

   Read the `detection_rules` section from the selected template's catalog entry (`templates/catalog.json`) or frontmatter (e.g. `templates/web-app.md`). It defines:
   - `always_include` — chapters always present regardless of code content
   - `chapters[]` — standard chapters with detection rules
   - `extra_chapters[]` — chapters to auto-add when detected
   - `granularity` — merge/split rules based on code volume

   The catalog entry and the template frontmatter are kept in sync by `scripts/validate-template-catalog.py`; either is authoritative for reading, prefer `templates/catalog.json` for programmatic access.

   Load the reference format from `references/template-catalog.md` → "Detection rules" section.

   #### 3b. Run detection checks

   For each standard chapter with detection rules, execute the checks in order:

   ```
   for each chapter in detection_rules.chapters:
       if chapter.id in always_include:
           status = "included" (skip detection)
           continue

       detected = false

       # Check directories
       if chapter.detection.dirs:
           for dir in dirs:
               if glob("{dir}/**") has matches:
                   detected = true; break

       # Check files
       if not detected and chapter.detection.files:
           for pattern in files:
               if glob(pattern) has matches:
                   detected = true; break

       # Check patterns (rgs / deps / files)
       if not detected and chapter.detection.patterns:
           for pattern_group in patterns:
               if pattern_group.rgs:
                   for pattern in rgs:
                       if rg(pattern) has match:
                           detected = true; break
               if pattern_group.deps:
                   for dep in deps:
                       if dep found in package manifests:
                           detected = true; break
               if pattern_group.files:
                   for pattern in files:
                       if glob(pattern) has matches:
                           detected = true; break

       status = "included" if detected else "excluded"
       if not detected and chapter.detection.optional:
           status = "included" with optional flag
   ```

   Then check `extra_chapters` — any whose detection rules match get `status = "auto_added"` at the specified `insert_after` position.

   Then check `granularity` merge/split rules using the counts collected during detection:

   ```
   # Merge check (small project)
   for each merge_rule in granularity.merge:
       if conditions met (e.g. screens ≤ screens_max):
           mark chapters as "merged" → new combined chapter

   # Split check (large project)
   for each split_rule in granularity.split:
       if conditions met (e.g. entities ≥ entities_min):
           mark chapter as "split" → N sub-chapters
   ```

   **Reuse** the file-tree and line-count data already collected in recon-report.md rather than re-running glob/rg from scratch — the detection is a secondary scan on top of the primary reconnaissance.

   #### 3c. Persist the detection result

   Write the result to `goal.json.customized_chapters`:

   ```json
   {
     "customized_chapters": [
       {"id": "ch-overview", "title": "Overview", "status": "included", "note": null, "confidence": "always"},
       {"id": "ch-auth", "title": "Authentication and authorisation", "status": "excluded", "note": "認証フレームワーク・認証関連コードが見つかりませんでした", "confidence": "high"},
       {"id": "ch-background-jobs", "title": "Background jobs", "status": "auto_added", "note": "Sidekiq設定検出", "confidence": "high"}
     ],
     "chapter_actions_applied": {
       "excluded": ["ch-auth"],
       "auto_added": ["ch-background-jobs"],
       "merged": [],
       "split": []
     }
   }
   ```

   #### 3d. 🆕 Template fit critical review (recon-driven)

   After detection_rules have produced `customized_chapters`, but before presenting to the user,
   critically evaluate whether the **selected template itself** remains the best fit.

   **Purpose**: The detection_rules only toggle individual chapters on/off. They cannot detect
   that the *entire template* is wrong for this codebase, or that structural changes beyond
   chapter-level toggles are needed. This step fills that gap.

   **Procedure**:

   1. **Read the recon report** — re-read `recon-report.md` alongside `customized_chapters`.

   2. **Evaluate template fit** against these dimensions:

      | Dimension | Question | Evidence source |
      |:----------|:---------|:---------------|
      | **Category match** | Do the recon findings still match the template's selection criteria? | recon-report.md package/language/detection findings vs template-catalog.md criteria |
      | **Structural anomalies** | Are there codebase patterns the template doesn't model? (e.g. DDD Bounded Contexts, event sourcing, CQRS, plugin architecture, multi-tenancy) | Directory structure, package patterns, naming conventions from recon |
      | **Chapter order tension** | Does the recon evidence suggest a different reading order? (e.g. auth is the central concern → move auth forward) | Detect which areas have the most code / complexity / business logic |
      | **Always-include drift** | Are any `always_include` chapters likely irrelevant for this codebase? (edge case: an infra spec with no deploy pipeline) | recon evidence vs always_include list |
      | **Template mismatch** | Would a different template or composite pattern serve better? (e.g. detected as web-app but API dominates) | Ratio of UI code vs API code, entry-point patterns |

   3. **Classification of result**:

      | Classification | Meaning | Action |
      |:--------------|:--------|:-------|
      | 🟢 **Confirmed** | Template and detection results are a good fit | Proceed to step 3e (presentation) |
      | 🟡 **Adjusted** | Template is OK but recon suggests structural changes beyond toggles | Modify `customized_chapters` (reorder, rename, regroup) and document reasoning in `goal.json.customized_chapters[].review_note` |
      | 🔴 **Switch template** | Recon evidence contradicts the template category | Go back to Step 2 with a new recommendation + explanation |
      | ⚪ **No template** | Codebase doesn't fit any template (unique architecture) | Build a custom chapter structure from recon evidence (see 3d-2) |

   4. **For 🟡 Adjusted — structural modifications beyond detection_rules**

      Beyond the toggles already handled by detection_rules, the following structural
      changes may be applied based on recon evidence:

      | Recon finding | Possible structural change |
      |:-------------|:-------------------------|
      | DDD project structure (domain/application/infrastructure) | Split relevant chapters per Bounded Context |
      | Auth is the most complex subsystem | Move auth chapter earlier (closer to Overview) |
      | Heavy test/BDD directory (`features/`, `spec/`) | Add "Testing strategy" chapter or merge into Operations |
      | API surface dominates (OpenAPI, GraphQL schema) | Elevate endpoint chapter, merge UI chapters |
      | Plugin/extension architecture detected | Add "Extension points" chapter after Architecture |
      | Monorepo with shared lib (but single-scope) | Add "Internal library dependencies" section to Architecture |

      Record every structural change with a rationale note:
      ```json
      {
        "id": "ch-auth",
        "title": "Authentication and authorisation",
        "status": "included",
        "note": null,
        "confidence": "high",
        "review_note": "Auth is the most complex subsystem (~30% of code) → moved to position 4 (after Architecture)"
      }
      ```

   5. **For ⚪ No template — build custom chapter structure from recon**

      When no template fits, construct a chapter structure directly from recon evidence.
      The following rules must be followed:

      - **Always include**: Overview, Feature specifications, Architecture overview, Design decisions, Known constraints (same as `always_include`)
      - **Reader comprehension flow**: Overview → Features → Structure → Details → Rationale → Boundaries
      - **Chapter naming**: Use specback naming conventions (noun phrase, title case, 2-5 words)
      - **Evidence-derived chapters**: Each chapter must be traceable to a recon finding (directory, package, pattern). No speculative chapters.
      - **Granularity**: 6-15 chapters. Fewer for small codebases (≤200 files), more for large ones.
      - **Validate**: Does every major recon finding (framework, architecture pattern, key directories) map to at least one chapter?

      Set `goal.template_used: "custom"` and populate `customized_chapters` with the
      custom structure. Include `"review_note": "Custom structure built from recon — no applicable template"` on each chapter.

   6. **Log the review result**

      Add to `recon-report.md` under a new `## Template fit review` section:

      ```markdown
      ## Template fit review

      - **Template assessed**: web-app v0.1.1
      - **Classification**: 🟡 Adjusted
      - **Rationale**: Auth subsystem dominates (~30% of code, complex role hierarchy).
        Moved auth chapter from position 8 to 4 for reader comprehension.
      - **Custom changes**:
        - ch-auth: reorder (8→4), review_note: "Auth is the most complex subsystem"
      ```

   #### 3e. Present the customised chapter list to the user

   Show the result with icons and notes:

   ```
   📋 コード分析による章構成の自動カスタマイズ結果:

     ✅ 第1章: 概要（常時含める）
     ✅ 第2章: 機能仕様（常時含める）
     ✅ 第3章: アーキテクチャ概要（常時含める）
     ✅ 第4章: クラス/モジュール設計（常時含める）
     ✅ 第5章: 画面一覧・遷移（app/views/ 検出）
     ✅ 第6章: ルーティング（config/routes.rb 検出）
     ❌ 第7章: データモデル（migration/models 未検出 → 除外）
     ❌ 第8章: 認証・認可（認証FW未検出 → 除外）
     ✅ 第9章: 外部インターフェース（HTTPクライアント使用検出）
     ✅ 第10章: 運用設定（Dockerfile 検出）
     ✅ 第11章: 設計判断（常時含める）
     ✅ 第12章: 既知の制約（常時含める）
     ➕ 第13章: バックグラウンドジョブ（Sidekiq検出 → 自動追加）

     除外された章: データモデル, 認証・認可
     自動追加された章: バックグラウンドジョブ

   （以下、merge/splitがトリガーされた場合の表示例）

     小規模 project:
       🔗 第4章: Webインターフェース（画面3 + ルート8 → Screens+Routes統合）
       🔗 第5章: 運用設定（認証login/logoutのみ → Operations内包）

     大規模 project:
       🔀 第7章: データモデル（Entity数45）
           └ 第7-a: Core entities（30エンティティ）
           └ 第7-b: Analytics/Reporting（15エンティティ）

   ```

   Use AskUserQuestion with choices:
     - "I confirm — proceed with this structure" → keep customized_chapters
     - "Let me adjust (add/remove/rename chapters)" → proceed to manual adjustment
     - "Restore all excluded chapters" → revert to full template outline

4. **Adjust the chosen template (if needed)**  
   Only reached if the user chose "Let me adjust" in the previous step, OR the user is applying further manual customisations on top of the automated result.
   - Display the chapter outline and ask "Are there chapters to add, remove, or rename?".
   - Reflect any additions/removals in `goal.json.customized_chapters`.

5. **🆕 Monorepo detection and scope setup**

   After the template is finalised, check whether the target codebase is a monorepo containing multiple independent systems.

   **Detection heuristics** (check in order):
   1. **Workspace manifests**: Does `pnpm-workspace.yaml`, `lerna.json`, `turbo.json`, `nx.json`, or `package.json.workspaces` exist?
   2. **Multi-service directories**: Do `services/`, `apps/`, or `packages/` directories contain independent package manifests (`package.json`, `setup.py`, `go.mod`, `composer.json`)?
   3. **Multiple entrypoints**: Are there multiple `main` files, `Dockerfile`s, or deployment configs across different subdirectories?

   If ANY heuristic matches, use `AskUserQuestion` to present the option:

   ```
   This repository appears to contain [N] independent systems/components.
   How would you like to generate specs?

   1. Individual specs per system (recommended for monorepos)
      → Each system gets its own state dir and spec output
   2. One combined spec for the whole repo
      → All systems merged into a single document
   3. Select specific systems only
      → Free-form: list the systems you want
   ```

   - If the user chooses option 1 or 3, set `goal.multi_scope = true`.
   - **Auto-detect scope boundaries**: For each candidate system, determine its root directory and assign a short `name` slug (derived from the directory name, e.g. `auth`, `payment`, `frontend`). Use the following rules:
     - `services/{name}/` or `apps/{name}/` → `{name}`
     - `packages/{name}/` with a package manifest → `{name}`
     - Top-level directories with their own `Dockerfile` → `{dir_name}`
   - Populate `goal.scopes = [{"name": "auth", "root": "services/auth"}, ...]`
   - **When option 3** (select specific systems): parse the user's free-form input, match each entry against detected systems, and include only the matched ones. If a name doesn't match, ask for clarification.
   - **Confirmation**: Show the final scope list and ask for confirmation:
     ```
     Scopes to generate:
       auth      → services/auth/    (Web application spec)
       payment   → services/payment/ (API service spec)
       frontend  → apps/frontend/    (Library/SDK spec)

     OK? (yes / redo)
     ```
   - Each scope may have a **different template** (detected independently in Phase 2).

   **State isolation**: When `multi_scope == true`:
   - Each scope uses its own state directory: `{output_dir}/{scope.name}/.specback/` (e.g. `docs/auth/.specback/`)
   - `.skill-path` is shared (symlink or copy): `ln -sf $(cat {output_dir}/.specback/.skill-path) {output_dir}/auth/.specback/.skill-path`
   - The `{output_dir}/.specback/` stores only the shared `goal.json` and `state.json` (which tracks `current_scope` across phases).
   - Script invocations use `--specback-dir {output_dir}/{scope.name}/.specback`.

   When `multi_scope == false` (default), proceed with the original `{output_dir}/.specback/` flow unchanged.

6. **Register high-level questions**
   - Add the fundamental questions surfaced during reconnaissance (questions that block big-picture understanding) into `questions.json`.
   - Examples:
     - What business problem is this system trying to solve?
     - How wide is the scope (which module inside the monorepo)?
     - When existing docs disagree with the code, which is authoritative?
   - See "Question Bank operation" below for the structure used at registration.

7. **🆕 depth-mode & tone decision (scale-based)**
   - Record the **total file count** and **estimated code lines** observed during reconnaissance at the top of `recon-report.md`. Persist as `total_files` and `total_lines` in `.specback/state.json`.
   - **If total_lines > 500**, ask the user with `AskUserQuestion` to choose a **depth mode**:
     - `comprehensive`: full chapter set (all chapters from the template). **Recommended only when exhaustive coverage is required (audit, regulatory).** Estimated 2–4 hours for most projects (Phase 3 parallel investigation scales with concurrency).
     - `outline` (**recommended default**): minimal chapters — tables + Mermaid diagrams + deep-dive candidate list. Details produced on-demand in dialogue after Phase 6. **Best for typical use.**
     - `interactive`: same flow as outline, plus continued deep-dive acceptance after Phase 6 completes. **Use when a team will continue referencing the spec.**
   - **If total_lines ≤ 500**, default to `outline` automatically (no question for depth_mode). The user may still override.
   - Then, ask the user to choose a **writing tone** (regardless of depth_mode):
     - `concise`: compact. Facts, REFs, and essential explanations only. No padding prose.
     - `thorough` (**default**): more detailed explanations. Include background, rationale, and alternatives where relevant.
   - Persist both to `.specback/goal.json` as:
     - `depth_mode: "comprehensive" | "outline" | "interactive"`
     - `tone: "concise" | "thorough"`
   - Phases 2 / 3 / 4 / 6 branch on these values.
   - Question wording example:
     > The target codebase has ~{total_lines} lines across ~{total_files} files. Choose a depth mode for the spec.
     > (Outline → deep-dive items of interest later, in practice, is recommended.)
     >
     > Writing tone:
     > → concise (compact, facts + REFs only)
     > → thorough (detailed explanations) [default]

7. **Phase 1 complete**
   - Update `state.json` and proceed to Phase 2.

### Phase-specific cautions
- Reconnaissance follows the principle "shallow but wide". Detailed logic understanding is deferred to Phase 3.
- Without noise exclusion (`node_modules`, `vendor`, `.git`, etc.) the output explodes.
- If the user brings their own template, you may point out the recommended template differs, but the decision is the user's.

---
