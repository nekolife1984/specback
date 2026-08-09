## Phase 4: Verify (checks + loopback)

### Purpose
Run inventory cross-check, per-chapter quality metrics, MECE check, and consistency checks automatically, looping failing chapters back to Phase 3.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, the following steps apply:

1. **Determine the current scope**: Read `goal.current_scope` (index into `goal.scopes[]`). Let `scope = goal.scopes[current_scope]`.
2. **Set scope-specific paths**:
   - `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` (e.g. `docs/auth/.specback`)
   - `TARGET_ROOT = scope.root` (e.g. `services/auth`)
   - `OUTPUT_DIR = "{output_dir}/{scope.name}"` (e.g. `docs/auth`)
3. **Ensure `.skill-path`**: `mkdir -p {SPECBACK_DIR} && ln -sf $(cat {output_dir}/.specback/.skill-path) {SPECBACK_DIR}/.skill-path`
4. **Run the phase procedure below** using `{SPECBACK_DIR}` as the specback directory (for scripts: `--specback-dir {SPECBACK_DIR}`) and `{TARGET_ROOT}` as the target codebase root (for source-map: `--target {TARGET_ROOT}`).
5. **On completion**: Increment `goal.current_scope` in `{output_dir}/.specback/goal.json`. If `current_scope >= scopes.length`, reset to `0` (all scopes done for this phase).
6. **Resume support**: After each scope completes, save `state.json` with `current_scope` so the session can resume from the correct scope.
7. **At the START of this phase**: If `goal.current_scope > 0` and `goal.multi_scope == true`, this is a resume — skip already-completed scopes and start from `goal.current_scope`.

When `goal.multi_scope == false` (default), run the phase procedure once with `{output_dir}/.specback/` and the project root as before.

---


### Procedure

1. **Generate trace.json**
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/build-trace.py" --specback-dir {output_dir}/.specback --output-dir {output_dir} --target-dir-for-required drafts
   ```
   This resolves every `<!-- REF: path:line -->` in `{output_dir}/.specback/drafts/*.md` to a SRC unit and produces the MECE aggregation.

2. **Run coverage-check.py (mandatory; exit code is binding)**
   ```bash
   python "$(cat {output_dir}/.specback/.skill-path)/scripts/coverage-check.py" \
     --specback-dir {output_dir}/.specback \
     --output-dir {output_dir} \
     --target-dir-for-required {output_dir}/.specback/drafts \
     --output-format text
   ```
   This invocation is **non-optional**. The script's exit code is the gate:

   **`--output-dir` vs `--target-dir-for-required` resolution:**

   | `--target-dir-for-required` | `--output-dir` | Resolved path | Notes |
   |----------------------------|----------------|---------------|-------|
   | `{output_dir}/.specback/drafts` | `{output_dir}` | `{output_dir}/.specback/drafts/` ✅ | Drafts always live here in Phase 4 |
   | `drafts` | `{output_dir}` | `{output_dir}/drafts/` ❌ → **fallback**: tries `drafts/` → fails ❌ | Wrong dir; drafts are under `{output_dir}/.specback/drafts/` |

   Fallback resolution: when `--output-dir / --target-dir-for-required` does not exist, the script automatically tries `--target-dir-for-required` as a standalone path (absolute or relative). This allows passing `{output_dir}/.specback/drafts` directly without path arithmetic.
   - `0` → all checks pass; Phase 4 may proceed.
   - `1` → at least one check failed; go to step 3 (loopback). Recording `all_quality_gates_passed: true` in `state.json` while exit is 1 is forbidden.
   - `2` → required artefacts (e.g. `inventory.json`) missing; surface to user.

   **`--code-block-line-weight` (default: `0.5`):**
   Controls how non-blank lines inside fenced code blocks contribute to the body-lines count.
   - `0.5` (default): every two code-block lines count as one body line
   - `1.0`: code-block lines count as full body lines
   - `0.0`: code-block lines are excluded entirely (original behaviour)
   
   This prevents chapters with substantial code examples (API specs, internal structure, usage examples) from being penalised solely for having many code blocks. The weight is adjustable per project needs via the CLI flag.

   Checks performed (12 total):
   - inventory count (min: `max(50, files / 20)`)
   - macro-type INV ratio (max 20%)
   - covered_by fill rate (90%)
   - per-chapter body lines (>= 200), <!-- REF: ... --> count (>= 10), code blocks (>= 3), Mermaid (>= 1), Sources Read items (>= 5) — **applied only to standard chapters that are NOT excluded by Phase 1 detection (excluded chapters are skipped entirely by Phase 2 and must not appear in the draft directory); user_custom chapters are exempt**
   - questions count (≥ 10), open ratio (≤ 20%)
   - MECE coverage (≥ 70%)
   - **Check 12 — User-custom deliverables**: every filename in `goal.json.user_custom_deliverables` must exist in the target directory (`{output_dir}/.specback/drafts/` in Phase 4, `{output_dir}/` in Phase 6) AND have a non-empty body (≥ 10 non-blank lines outside code fences).

2.5. **Check Markdown quality (markdownlint-cli2) — optional gate**

   Runs markdownlint-cli2 against all generated draft specs to catch structural issues (heading consistency, list formatting, code block style, trailing whitespace, etc.).

   This step runs **after** coverage-check.py passes (exit 0) and is a **non-blocking advisory** gate — violations are reported but do NOT trigger a loopback to Phase 3. The agent may optionally fix fixable issues with `--fix` if the user requests it.

   ```bash
   bash "$(cat {output_dir}/.specback/.skill-path)/scripts/verify-markdownlint.sh" \
     --specback-dir {output_dir}/.specback
   ```

   | Exit | Meaning | Action |
   |:----:|:--------|:-------|
   | `0` | All markdown checks passed | Proceed |
   | `1` | Violations found | Report to user; continue (non-blocking) |
   | `2` | Config file missing or error | Report error; continue (no gate) |

   **Configuration**: see `references/markdownlint-config.yaml` for the rule set. Key design:
   - `MD013` (line-length): disabled — spec prose is long-form narrative
   - `MD026` (trailing punctuation in headings): disabled — Japanese specs use 。and ．in titles
   - `MD033` (inline HTML): disabled — `<!-- REF: ... -->` and `<!-- meta: ... -->` are structural
   - All other structural rules (heading order, list consistency, code block style) are enforced.

   **Skipping**: the step is automatically skipped if the target directory (`{output_dir}/.specback/drafts/`) does not exist (e.g. Phase 4 called without Phase 3 having run).

3. **Failure → loop back to Phase 3**
   - When exit code is 1, read the "gate decision" section of the output and:
     1. Identify the failed chapter (e.g. `chapter 05-data-model.md: <!-- REF: ... --> count is 7 < required 10`)
     2. **Read additional sources** corresponding to the chapter's `assigned_inventory_ids`
     3. Add to Sources Read, raise `<!-- REF: ... -->` count, thicken the body
     4. Re-run coverage-check.py
   - For `user_custom` chapters that are missing or empty, treat the failure the same way: return to Phase 3 and fill the chapter using `wbs.json.chapters[].source_intent` and any Phase 5 dialogue answers that pertain to it.
   - Maximum iterations: **3**. If a `kind: "standard"` chapter still fails after 3 attempts, record it in `99-unresolved.md` as "insufficient quality" and continue. A failing `kind: "user_custom"` chapter must NOT be silently demoted to `99-unresolved.md`; instead, prompt the user via `AskUserQuestion` to (a) keep retrying, (b) reduce scope, or (c) abandon the deliverable explicitly.

4. **Cross-reference verification**
   - Check whether any cross-chapter inconsistency exists for the same concept.
   - File inconsistencies into `questions.json` with `priority: critical`.

5. **Deduplicate questions**
   - Detect duplicates across the entire Question Bank.
   - Merge only the "obviously identical"; flag the "similar but subtly different" as groups for Phase 5 confirmation.

6. **Save the verification report**
   - Save `coverage-check.py --output-format json` output to `{output_dir}/.specback/coverage-report.json`.
   - Save a human-readable version to `{output_dir}/.specback/coverage-report.md`.

7. **Doubt-pass: adversarial review subphase (opt-in)**
   This subphase runs after coverage-check passes. It questions every major claim in the generated draft specs by re-reading the source code in a "fresh context" (as if the code were seen for the first time) and checking whether the interpretation is correct.

   **7a. Determine doubt targets**
   Apply the doubt-trigger ruleset to select which claims to review:

   | Trigger | Scope | Condition |
   |---------|-------|-----------|
   | 🔴 **ASSUMED** | Every chapter containing 🔴 ASSUMED markers | Always selected (default: auto-include) |
   | 🟡 **INFERRED chain ≥ 3** | Any claim inferred from ≥3 sequential code readings without verification | `doubt_inferred_chain_length >= 3` (configurable via `goal.json.doubt.inferred_chain_min`) |
   | 🟢 **VERIFIED with comment conflict** | Code comments and spec interpretation diverge | Detected via `<!-- REF: ... -->` line re-read — if the comment contradicts the claim in the spec |
   | **Cross-chapter axiom** | A statement shared across multiple chapters that has no single `<!-- REF: ... -->` backing | Any statement with zero `<!-- REF: ... -->` citations that appears in ≥2 chapters |

   Read `docs/doubt-pass.md` for the full trigger definitions, threshold tuning, and examples.

   **7b. Execute doubt-protocol per target**
   For each selected claim, run the 5-step protocol. Claims from 🔴 ASSUMED chapters and cross-chapter axioms take priority; INFERRED chains next; VERIFIED conflicts last.

   ```
   CLAIM → EXTRACT → DOUBT → RECONCILE → STOP
   ```

   - **CLAIM**: Isolate one specific claim from the draft (e.g. "`IssuesController#create` returns a 201 status on success"). Record the claim verbatim with its source chapter and `<!-- REF: ... -->`.
   - **EXTRACT**: Identify the exact code file(s) and line(s) that support the claim. Only use the `<!-- REF: ... -->` citations already in the draft — do not add new citations while extracting.
   - **DOUBT**: Re-read the extracted code in a **fresh context** (do not reuse any prior reading notes, cached observations, or earlier interpretations from Phase 3). Evaluate:
     - Does the code actually say what the claim asserts?
     - Is there an edge case, error path, or alternative flow the claim misses?
     - Does a nearby code section (the 5 lines before/after the cited range) contradict the claim?
     - Is the claim's confidence label (🟢/🟡/🔴) appropriate given the re-read evidence?
   - **RECONCILE**: For each discrepancy found in DOUBT:
     - If the claim is **wrong** → add a corrective note to the draft chapter and push the chapter back to Phase 3 (loopback, counting toward the 3-attempt limit).
     - If the claim is **imprecise** → adjust the wording and tighten the `<!-- REF: ... -->` range.
     - If the claim is **correct but under-confident** → upgrade the marker (🔴→🟡 or 🟡→🟢).
   - **STOP**: Assign a **confidence score** (1.0 = certain, 0.0 = contradictory material found) to the claim. Record it in `{output_dir}/.specback/doubt-report.json`.

   **7c. Doubt-report output**
   After processing all selected targets, generate `{output_dir}/.specback/doubt-report.json`:

   ```json
   {
     "doubt-pass": true,
     "claims_reviewed": 5,
     "claims_passed": 3,
     "claims_needing_correction": 2,
     "confidence_avg": 0.72,
     "doubt_resolution_rate": 0.6,
     "failures": [
       {
         "chapter": "02-feature-specifications.md",
         "claim": "IssuesController#create returns 201 on success",
         "confidence": 0.3,
         "discrepancy": "Code raises 422 on validation failure; the claim only describes the happy path",
         "recommendation": "Split into success (201) and failure (422) sub-entries"
       }
     ]
   }
   ```

   **7d. Question bank integration**
   - Every discrepancy found during DOUBT that cannot be resolved by re-reading alone is pushed into `questions.json` with `category: "architecture_decision"` (or `"spec_missing"` if a gap is found) and `severity: "critical"`.
   - If the same claim fails doubt in consecutive sessions (verified by `doubt-report.json` history), escalate the question to `severity: "critical"` and add `[NEEDS SME]` in the chapter regardless of Phase 5 timing.

   **7e. Opt-out**
   - Set `goal.json.doubt.enabled: false` to skip the entire doubt-pass subphase.
   - Set `goal.json.doubt.scope: ["assumed_only"]` to run doubt-pass only on 🔴 ASSUMED markers (skip INFERRED chains and VERIFIED conflicts).
   - Set `goal.json.doubt.max_claims` to limit the number of claims reviewed per run (default: 10).

   **7f. Relationship with Phase 5**
   - Doubt-pass catches what code re-reading alone can resolve. Questions that genuinely require SME judgement (e.g. business rationale, design intent from a past team member) are NOT resolved here — they flow to Phase 5 as normal.
   - Do NOT use doubt-pass as a substitute for Phase 5 dialogue. Its purpose is to reduce the burden on Phase 5 by self-resolving code-interpretation questions.

   See `docs/doubt-pass.md` for the full protocol reference, configuration schema, and troubleshooting.

8. **Phase 4 complete**
   - Once every chapter passes (or hits the 3-attempt qualitative limit) AND the doubt-pass subphase (if enabled) has completed, update `state.json` and proceed to Phase 5.

### Phase-specific cautions
- **Do not proceed to Phase 5 until coverage-check.py PASSes** (up to 3 loop iterations). Setting `phase_4.all_quality_gates_passed: true` is only allowed when the most recent `coverage-check.py` invocation returned exit code 0.
- The loopback is not "padding the prose" — its purpose is to **read more real code, add more citations, and thicken the explanation**.
- Missing cross-chapter inconsistencies makes Phase 5 dialogue explode. Squash them in Phase 4.
- **`coverage_rate` < 100% with `all_quality_gates_passed: true` is a contradiction** and is never permitted. If full coverage is impossible within 3 iterations, leave `all_quality_gates_passed: false`, record the unfinished chapters, and surface to the user instead of advancing.
- **Feature specifications chapter (Ch2) note**: This chapter often has a higher 🔴 ASSUMED ratio than other chapters because code is organised by layer, not by feature. The Phase 3 investigation compensates by using multiple grouping strategies (see `references/outline-tables.md`). The 🔴 ratio warning in `coverage-check.py` is **informational only** for Ch2; it does not block the Phase 4 gate. The body-length and REF-count requirements still apply in `comprehensive` mode.
- **Design decisions chapter note**: This chapter uses import analysis and pattern detection. The ADR section may have many 🔴 entries (design rationale is rarely in code). The 🔴 ratio warning in `coverage-check.py` is **informational only** for this chapter. Body-length and REF-count requirements still apply in `comprehensive` mode.
- **Doubt-pass does not replace coverage-check**: The doubt-pass subphase is an additional quality gate, not a substitute. `coverage-check.py` must still pass (exit code 0) before doubt-pass runs. A chapter that fails doubt (claims needing correction) is looped back to Phase 3, consuming one of the 3-attempt limit — a doubt-related loopback counts toward the iteration cap for that chapter.
- **Fresh context is critical**: The DOUBT step MUST re-read the code from scratch without referencing Phase 3 investigation notes. Reusing cached observations defeats the purpose of adversarial review and constitutes a quality breach.
- **Doubt-report.json is not a replacement for state.json**: The doubt report is informational. `state.json.phase_4.all_quality_gates_passed` still reflects `coverage-check.py` exit code 0; doubt-pass completion is recorded separately in `doubt-report.json.doubt-pass: true`.

---
