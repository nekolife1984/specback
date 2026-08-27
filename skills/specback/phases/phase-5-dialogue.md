## Phase 5: Refine via Dialogue

### Purpose
Through dialogue with the user, resolve uncertainty markers and the Question Bank, refining the spec.

> **⚠️ Phase 5 must not be skipped entirely.** At minimum, present the Question Bank overview to the user and resolve at least one critical or important question. Skipping Phase 5 without any user dialogue leaves uncertainty markers unresolved and degrades spec credibility. If the agent determines that all questions are low-priority, record the rationale in `state.json.phase_5.skip_reason` and proceed, but always attempt Stage 1 first.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:

1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**:
   - `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` (e.g. `docs/auth/.specback`)
3. **Ensure `.skill-path`** (validate → re-resolve): `SP="$(cat {output_dir}/.specback/.skill-path 2>/dev/null)"; [ -z "$SP" ] || [ ! -d "$SP/scripts" ] && echo "$PWD" > {output_dir}/.specback/.skill-path; mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the phase procedure below** using `{SPECBACK_DIR}` as the specback directory.
5. **On completion**: Increment `goal.current_scope` in `{output_dir}/.specback/goal.json`. If `current_scope >= scopes.length`, reset to `0` (all scopes done for this phase).
6. **Resume support**: After each scope completes, save `state.json` with `current_scope` so the session can resume from the correct scope.
7. **At the START of this phase**: If `goal.current_scope > 0` and `goal.multi_scope == true`, this is a resume — skip already-completed scopes and start from `goal.current_scope`.

When `goal.multi_scope == false` (default), run the phase procedure once with `{output_dir}/.specback/` as before.

### Procedure

`coverage-check.py` enforces `--max-open-ratio 0.2`, so leaving more than 20% of items as `open` blocks progression to Phase 6. Run all 3 stages.

#### Stage 1: Present the big picture (mandatory, once)

Ask the user **a single question** via `ask_user_question`:

```
Unresolved questions: N items
Per-category breakdown: business_rule X, architecture Y, data_model Z, ...
Per-severity breakdown: critical X, important Y, nice-to-have Z

Pick a progress mode:
- Answer every question one by one (most thorough)
- Answer only critical ones (faster)
- Mark every remaining question abandoned and skip to Phase 6 (fastest, lower quality)
```

choices: `["Answer all", "Critical only", "Skip with abandoned"]`, allow_free_text: true

#### Stage 2: Present critical clusters (mandatory, at least 3 clusters)

If Stage 1 selects "Answer all" or "Critical only", group the critical questions into related clusters.
**At least 3 clusters** are required (if fewer naturally, split mid-grain).

Present each cluster as one `ask_user_question`:
```
Business-rule cluster A (#Q-005, #Q-008, #Q-012)
These are questions around the purchase flow.
Answer them in sequence?

- Answer in sequence (recommended)
- Postpone this cluster
- Mark this cluster abandoned
```

#### Stage 3: Per-question dialogue (the rest of the questions)

Present each question via `ask_user_question`:

- **question**: the relevant code excerpt + tentative assumption + risk
- **choices**: `["Inference is fine (reflect in spec)", "Enter the correct answer", "Need SME confirmation, skip", "Cannot ever resolve (abandoned)"]`
- **allow_free_text**: true

Reflect the answer into the corresponding entry in `questions.json`:
- `Inference is fine` → `status: answered`, `answer: <tentative inference>`
- `Enter the correct answer` → `status: answered`, `answer: <user free-form input>`
- `Need SME confirmation` → `status: skipped`
- `abandoned` → `status: abandoned`

### To prevent Phase 5 "padding" by the agent

**If `questions.json` has fewer than 10 entries**:
- Before starting Phase 5, review the Phase 3 drafts and extract at least 5 ambiguous spots into `questions.json`.
- Extraction angles:
  - Spots where the naming convention or design intent is unclear
  - Spots where the business rule can only be inferred
  - Spots where error-handling policy admits multiple interpretations
  - Special implementations diverging from framework defaults
  - Handling of unused / deprecated code

### Applying answers

- Reflect each answer into the corresponding chapter draft (remove or update uncertainty markers).
- Fill in `<!-- BLOCKED: see Q-NNN -->` sections.
- **Answers that define a new deliverable structure are actions, not notes.** If a dialogue answer fixes the contents/sections of a `kind: "user_custom"` chapter that is still empty (or fixes a new file the user introduced in Phase 5), the agent MUST:
  1. Update the corresponding `wbs.json.chapters[]` entry — set or refine `chapter_title`, `assigned_inventory_ids`, and append the answer text to `source_intent`.
  2. Push the chapter back to Phase 3 (re-open with `status: "pending"`) and run a `chapter-investigator` pass (or the in-line equivalent) to actually write the file.
  3. Re-run `coverage-check.py` after Phase 3 finishes. Only when the file exists with body content may the chapter be marked `status: "done"`.
- Recording the user's answer in `99-unresolved.md` or in `state.json.phase_5.user_feedback` **without** triggering chapter creation is a contract violation: the user asked for a deliverable, not for a note about a deliverable.

### Re-reconnaissance (only when needed)

If the answer makes additional investigation necessary, re-read the relevant code with the Read tool as an extra step in Phase 3 and update the chapter.

### Phase 5 completion criteria

Satisfy `coverage-check.py`'s `--max-open-ratio 0.2` criterion:
- At least 80% of all questions are `answered` / `skipped` / `abandoned`
- Strictly less than 20% remain `open`
- Continue Phase 5 until this is reached.

### Phase 5 skip prevention (mandatory)

Phase 5 dialogue must actually happen. Recording `phase_5.status: "complete"` while:
- `questions.json` contains ≥ 20% of questions with `status: "open"`, OR
- Zero `AskUserQuestion` calls have been emitted in Phase 5, OR
- No question entry in `questions.json` has a populated `answer` field

— is a contract violation. Each of these states is an automatic Phase 5 fail; the agent must restart the 3-stage dialogue, not advance to Phase 6.

Concretely, before declaring Phase 5 complete the agent MUST:
1. Count `open` vs total questions. If `open / total > 0.2`, continue dialogue.
2. Verify at least the **Stage 1 overview** AND the **Stage 2 critical clusters** dialogues were actually presented to the user via `AskUserQuestion` (Stage 3 individual questions for the residual). Internal notes or `state.json.phase_5.user_feedback` strings do NOT substitute for actual dialogue.
3. Record per-question `answered_by` (user vs. agent inference) and `answered_at` (real UTC timestamp). Bulk-marking 50 questions as "answered" without dialogue is a contract violation.

The Phase 6 intent-vs-delivery audit re-verifies these constraints; failure routes back to Phase 5.

### Phase-specific cautions
- Skipping Stage 1 / Stage 2 and doing only Stage 3 is forbidden (the user loses the big picture).
- Demanding SME-grade answers for every question breaks the dialogue. `nice-to-have` items are allowed to remain as inferences.
- `abandoned` is reserved for "truly unanswerable in the long term" cases — do not abuse it as a shortcut.
- **Bulk marking dozens of questions as `answered` without dialogue is a contract breach** — the user did not answer them; the agent silently dropped them. The correct action is either to actually run the dialogue, or to honestly mark them `abandoned` with a reason.

---
