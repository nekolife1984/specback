## Phase 6.5: Interactive Deep-Dive

### Purpose

In `outline` / `interactive` modes, the spec at the end of Phase 6 is only "overview tables + Mermaid + deep-dive candidates". **The user reading the spec points out items of interest and asks for on-the-spot deep-dives** — that is the essence of these modes. Phase 6.5 holds the agent in a **deep-dive acceptance state**, waiting for explicit user instructions, until the env is closed.

### 🆕 Multi-scope execution

When `goal.multi_scope == true`, run the procedure below for the current scope (read from `goal.current_scope`). Set `SPECBACK_DIR = "{output_dir}/{scope.name}/.specback"` and use scope-specific draft paths (`{output_dir}/{scope.name}/.specback/drafts/deep/`). On completion, increment `goal.current_scope`.

When `goal.multi_scope == false` (default), run once with `{output_dir}/.specback/` as before.

### Procedure

After the Phase 6 completion report, the agent emits the following message and **waits for input**:

```
✅ Overview spec generation is complete (X chapters / Y tables / Z deep-dive candidates).

Check the "Deep-dive candidates" section at the end of each chapter.
For items of interest, instruct like this:

- By candidate ID:  "Deep-dive D-001" / "D-007"
- By entity ID:    "Tell me more about M-013 Issue" / "C-018 ProjectsController"
- By natural text: "Explain the authorisation model" / "How does Issue notification delivery work?"

To end the deep-dive mode, reply "end" / "complete" / "OK, done".
```

### Recognising and processing instructions

Recognise user input via the following patterns:

1. **Explicit ID (highest priority)**: matches `D-NNN` / `M-NNN` / `C-NNN` / `T-NNN`, etc. → look up the row/candidate in `wbs.json` / `inventory.json` / per-chapter tables → obtain the file and overview.
2. **Direct entity name**: `Issue class` / `ProjectsController`, etc. → identify the file via `grep`.
3. **Natural-language topic**: keywords like `authorisation` / `notification` / `payment` → keyword-search the relevant chapters/table rows, present the top 3 to the user, and ask "Which one do you want to deep-dive?".

### Generating a deep-dive chapter

Once the deep-dive target is fixed:

1. Launch the `chapter-investigator` sub-agent via the `task` tool.
2. Sub-agent prompt:
   - Target entity / candidate ID and overview
   - List of related real source files
   - "Write 1 deep-dive chapter" (≥ 10 REFs, ≥ 1 Mermaid; body length guided by `goal.tone`)
   - Output path: `{output_dir}/.specback/drafts/deep/D-NNN-{slug}.md` or `M-NNN-{slug}.md`
3. Display the key findings returned by the sub-agent in the main thread.
4. **Update traceability.md** (append the deep-dive chapter).
5. **Update the relevant row in the original Layer 1 chapter**: bump the confidence from 🟡/🔴 → 🟢, add a "see deep-dive `D-001`" link.
6. Report completion and return to the input-waiting state.

### Ending

When the user sends a completion word ("end", "complete", "OK, done", etc.):

1. Update `state.json` with `phase_6_5_completed_at`.
2. Re-generate `final/` (consolidating the deep-dive chapters).
3. Update final/traceability.md to the final version.
4. Close the env with a thank-you message.

### Phase-specific cautions

- **While waiting for user input, the agent does NOT poll or self-progress**. It moves only after an explicit instruction.
- If a deep-dive is **requested for a target already covered**, surface the existing deep-dive chapter and ask "Regenerate it?".
- Sub-agent return values during deep-dive also follow the **mode B contract** (path + summary), not the full body.
- Be mindful of cumulative cost: each deep-dive equals roughly one comprehensive chapter. Periodically report "N deep-dives so far, cumulative cost ~$X".
- **内部ファイル非表示ルール**: Deep-dive モードでのユーザー応対時も、`wbs.json`、`inventory.json` などの specback 内部ファイルパスをユーザーに向けて発言しない。内部ルックアップは裏で行い、「該当する deep-dive 候補を確認しました」のようにユーザー向け表現にすること。

---
