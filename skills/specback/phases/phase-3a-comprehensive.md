## Phase 3a: Comprehensive Investigation

### Purpose

Full prose per chapter with `comprehensive` depth mode — applies STEP A through G below to every chapter in `wbs.json.chapters[]`.

### Procedure (comprehensive mode)

#### STEP A: Read the sources (mandatory; skipping causes Phase 4 failure)

For every INV in that chapter's `wbs.json.chapters[*].assigned_inventory_ids`, **use the Read tool on the corresponding real source files**.

Every read file that backs a statement in the chapter body MUST be cited with an `<!-- REF: ... -->` marker (STEP B). There is no separate Sources Read section to maintain — the REF markers ARE the read-source record. (A `## Sources Read` section is optional; if present, its bullet lines count toward body_lines, so prefer omitting it.)

> Examples shown use Rails conventions. For catalogues covering PHP /
> Python (FastAPI / Django) / Java (Spring) / JavaScript & TypeScript
> (Express / Fastify / Hono) / Ruby on Rails, see
> `references/inventory-units.md`.

#### STEP B: Citation extraction (mandatory)

Extract at least **10 concrete citations** from the viewed code, in one of these formats:

**Format 1 — Direct path:line (traditional):**
```
<!-- REF: <workspace-relative path>:<start> -->
<!-- REF: <workspace-relative path>:<start>-<end> -->
```

**Format 2 — SRC-ID (recommended for stable references):**
```
<!-- REF: SRC-NNNN -->
```

The SRC-ID format references the source-map.json unit IDs (e.g. `SRC-0142`) and is automatically resolved by `build-trace.py`. Unlike path:line refs, SRC-ID refs survive code refactoring — simply regenerate the source-map after code changes and all refs remain valid. `fix-refs.py` skips SRC-ID refs (they have no line numbers to correct).

**Which format to use** (SRC-ID is recommended when the cited range maps to a source-map unit; `fix-refs.py --migrate-srcid` automates this decision):

| Condition | Use | Why |
|-----------|-----|-----|
| Cited path + range **exactly matches** a unit's `line_range` in source-map.json | `<!-- REF: SRC-NNNN -->` | Stable across refactors, auto-resolved |
| File not in source-map.json (README, configs, scripts, tests, docs…) | `<!-- REF: path:line -->` | No unit ID exists to reference |
| Range covers imports / docstrings / spans multiple units | `<!-- REF: path:line -->` | Converting would mis-locate the click target |
| Range only partially overlaps a unit | `<!-- REF: path:line -->` | Converting would be inaccurate |

`fix-refs.py --migrate-srcid` converts only the first case (exact match) and reports the rest with reasons, so remaining `path:line` refs are a deliberate, reviewable decision rather than an omission.

Examples:

```
<!-- REF: SRC-0142 -->
<!-- REF: SRC-0143 -->
<!-- REF: SRC-0001 -->
```

**Strict format requirements** (the UI's REF chip click-to-source feature parses these variant formats render as plain non-clickable text, breaking reviewer flow):

- Use **`<!-- REF: path:line -->`**, **`<!-- REF: path:start-end -->`**, or **`<!-- REF: SRC-NNNN -->`** only. The HTML comment markers, the `REF:` prefix, and the colon between path and line numbers (for path:line format) are all mandatory.
- The path is workspace-relative (`app/...` for an env with `archiveRoot = "myapp-main"`). Absolute paths are forbidden.
- Line numbers are integers. Use a single line (`:42`) when a single line is being cited; use a range (`:42-56`) when an extent matters. Do NOT use `L42`, `line 42`, ` lines 42-56`, parentheses, or any other decoration.
- **NEVER wrap a citation in parentheses or brackets** — `（<!-- REF: SRC-0142 -->）` or `(<!-- REF: SRC-0142 -->)`. The HTML comment renders as nothing, leaving a **visible empty `（）`** in the delivered spec. Write the citation bare: `...する<!-- REF: SRC-0142 -->。`
- Forbidden alternative forms include but are not limited to:
  - ❌ `Gemfile (lines 1-138)` — parenthesised line annotation
  - ❌ `[REF: Gemfile:1-138]` — **deprecated** legacy format (NOT parsed by the scripts; do not write it in specs)
  - ❌ `// app.js lines 1-5` — JS-style comment marker
  - ❌ `[REF: Gemfile L1-L138]` — leading `L`
  - ❌ `[REF: Gemfile, lines 1-138]` — comma + word "lines"
  - ❌ `[REF: Gemfile]` — no line numbers at all

Line ranges are precise (coarse ranges like `:1-500` are not acceptable). Cover class definitions, key methods, configuration values, callbacks, validations, exception handling, etc.

#### STEP C: Write the chapter body (required quality bar)

Incorporate the citations into the body. **Per-chapter mandatory requirements**:

| Item | Minimum | Verification script |
|------|---------|-------------|
| `<!-- REF: ... -->` count | >= 10 | coverage-check.py |
| fenced code block | >= 3 | coverage-check.py |
| Mermaid diagrams | >= 1 | coverage-check.py |

Body length is guided by `goal.tone`: `concise` -> compact prose (facts + citations); `thorough` -> include background, rationale, alternatives. No fixed line-count minimum.

Chapters that fail these are rejected in Phase 4 and loop back to Phase 3 for correction.

Around each `<!-- REF: ... -->`, add prose explaining "what is happening". Writing only what Rails/Laravel-style frameworks "typically do" is forbidden — write what the **actual code** does after reading it.

**Feature specifications chapter (Ch2)** — in addition to the general bar, each per-feature processing definition (2.2 / 2.3) must include the four spec-kit aligned sections (Issue #298):
- **Priority** — P1/P2/P3 importance for the product (P1 = core value proposition, P2 = important but not core, P3 = auxiliary). Derive from code evidence: call volume, criticality of the path, blast radius. REF optional (priority is a judgment, not a code claim).
- **Acceptance scenarios** — Given/When/Then derived from actual code behaviour; write 2–5 scenarios per feature, each carrying an `<!-- REF: ... -->` citation. Do not write scenarios for unimplemented features.
- **Independent test** — how to verify the feature in isolation (test file reference, or manual procedure).
- **Edge cases** — boundary values / exceptional inputs, kept separate from Error handling (Error handling = behaviour on failure (exceptions, error paths); Edge cases = boundary values / unusual inputs (empty, max length, duplicates, concurrency)).

Block ordering: `Overview` → `Priority` → `Trigger` → `Pre-conditions` → `Main flow` → `Alternative flows` → `Error handling` → `Edge cases` → `Acceptance scenarios` → `Independent test` → `Post-conditions` → `Related business rules` → `Related chapters` → `Confidence`.

#### STEP D: Uncertainty markers

Surface uncertainty in each statement:
- `<!-- CONFIDENCE: HIGH | MED | LOW -->`
- `<!-- ASK SME -->` (needs confirmation from a subject-matter expert)
- `<!-- ASSUMED: ... -->` (basis for the inference)

#### STEP E: Add detail questions to the Question Bank

Questions that surface while writing a chapter are added to `questions.json` (at least 1 per chapter). The final `questions.json` must contain **>= 10 items** (`coverage-check.py` enforces this).

Examples:
- Is this method retrying three times because of a technical constraint or a business requirement?
- What is the rationale for this configuration value?
- Is this commented-out code a transient remnant or part of the spec?

#### STEP F: Handle critical questions

If a critical question is hit, leave the corresponding section as `<!-- BLOCKED: see Q-042 -->` (empty). Loop back from Phase 5 (after dialogue) to Phase 3 to fill it in.

#### STEP G: Per-chapter sub-agent delegation (use when the `task` tool is available; recommended)

In environments where the `task` tool is available, **delegate each chapter to an isolated `chapter-investigator` sub-agent**. Writing every chapter directly in the main agent degrades context; investigating each chapter in its own context yields higher quality.

**Sub-agent invocation template:**

```
task(
  description="ch05 data-model investigation",
  prompt="""
You are the chapter-investigator handling Chapter 5: Data Model.

Target inventory_ids:
- INV-012 (Project)
- INV-013 (Issue)
- INV-014 (User)
- INV-015 (Role)

Corresponding real sources (Read these with the Read tool):
- app/models/project.rb
- app/models/issue.rb
- app/models/user.rb
- app/models/role.rb
- db/schema.rb (relevant portions)

Draft output path: {output_dir}/.specback/drafts/05-data-model.md

Quality bar:
- <!-- REF: SRC-NNNN --> (or <!-- REF: path:start-end -->) >= 10
- fenced code blocks >= 3
- Mermaid diagrams >= 1 (ER diagram)
- Body guided by tone: concise -> compact; thorough -> detailed

Format rules (coverage-check.py enforces these; violations loop back):
- Write `<!-- REF: ... -->` citations BARE — never wrap them in （） or (). A wrapped citation renders as a visible empty `（）`.
- Every read file you cite must be workspace-relative. NO absolute paths. A `## Sources Read` section is optional; if present, keep bullets to ``- `path` `` with workspace-relative paths only.

When done, return the chapter's key points + a list of detail questions raised.
The detail questions are material for the main agent to append into questions.json.

NOTE: If goal.output_language == "ja", render the chapter body, headings,
prose, and detail-question text in Japanese. Keep code blocks, file paths,
JSON keys, and <!-- REF: ... --> markers in English.
""",
  subagent_type="chapter-investigator"
)
```

**Important constraints**:

- **MANDATORY: Emit ALL chapter `task()` calls in a SINGLE assistant turn (parallel dispatch).**
  This is the most important rule of Phase 3. Read carefully — getting it wrong makes Phase 3 take **Nx** longer than it needs to.

  **WRONG (sequential — DO NOT DO THIS):**
  ```
  Assistant turn 1: task("ch-02 ...")             <- issue ONE task
                    <- wait for the Observation
  Assistant turn 2: task("ch-03 ...")             <- then issue the next
                    <- wait
  Assistant turn 3: task("ch-06 ...")
                    ...
  ```
  This pattern serialises everything. If each `chapter-investigator` takes 4 minutes and you have 8 chapters, Phase 3 takes ~32 minutes. The runtime's sub-agent concurrency pool is **wasted** because you only ever have 1 sub-agent in flight at a time.

  **CORRECT (parallel — REQUIRED):**
  ```
  Assistant turn 1: task("ch-02 ...")
                    task("ch-03 ...")
                    task("ch-06 ...")
                    task("ch-08 ...")
                    task("ch-11 ...")
                    ... (one task() per chapter, ALL emitted back-to-back)
                    <- yield, do NOT plan / think / write anything else
  Single Observation turn: receives all N results at once
  ```
  In one assistant turn, emit one `task()` tool call per chapter, back-to-back, with NO intervening text, NO `thought`-style narration, NO partial writes — just the task calls. Then yield control. The runtime fans them out concurrently and returns all Observations together when they complete.

  With a sub-agent concurrency of 5 and 8 chapters: ~2 batches of ~4 minutes each -> ~8 minutes total instead of 32. **Wall time scales by `1 / concurrency`**.

  **Self-check before emitting `task()`:**
  Have you written the prompts for **every** chapter that needs investigation in this Phase 3 round? If not, finish drafting them first, THEN emit them all together. Never emit one and "see how it goes" — that is the sequential anti-pattern.

  **Runtime concurrency mechanics.** The runtime's `Task` tool dispatches sub-agents in parallel up to its own pool. Other runtimes integrating the same skill should configure their own sub-agent pool similarly so the batch actually runs in parallel rather than being serialised at the executor level.

- **Prompt cache is NOT shared**: each sub-agent has an isolated LLM context, so token usage is 5-10x the main agent.
- **The sub-agent writes the chapter draft directly via the Write tool** (saved as a file, NOT returned in the task result text). The main agent reads the return value and appends detail questions into `questions.json`.
- **One `task()` per chapter**. Bundling all chapters into a single `task` call defeats the purpose (the isolated context per chapter disappears).

**When the `task` tool is unavailable**, the main agent performs STEP A-F itself per chapter.

### Phase-specific cautions
- Writing a chapter without reading the code is forbidden. You may cite only files you actually read (via `<!-- REF: ... -->` markers).
- Body length follows `tone`: concise -> compact; thorough -> detailed.
- >= 10 REFs must be satisfied.
- Cross-chapter consistency is checked in Phase 4.
- Do not hide uncertainty markers; keep them explicit in the draft.
- Do NOT declare Phase 3 complete unless **every** chapter in `wbs.json.chapters[]` has a non-empty body in `{output_dir}/.specback/drafts/` (at least 10 non-blank lines outside of code fences). Verify before updating `state.json`.
- Feature specifications chapter (Ch2): higher 🔴 ASSUMED ratio is expected and acceptable. The Phase 4 gate for confidence ratio does not apply to Ch2.
- Module architecture (overview) chapter: overview-level only. Keep short and skimmable.
- Design decisions chapter: uses import analysis, not per-file deep reading. Many 🔴 ASSUMED entries are expected.
- 内部ファイル非表示ルール: 生成ドキュメント本文内で `{output_dir}/.specback/inventory.json`、`{output_dir}/.specback/wbs.json` などの内部ファイルパスを参照しないこと。
