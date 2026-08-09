#### STEP G: Per-chapter sub-agent delegation with manifest relay (mode B)

When **Context Optimization mode B** is active, **delegate each chapter to an isolated `chapter-investigator` sub-agent** and process only the path and a summary from the return value. The body is written directly to `drafts/NN-slug.md` by the sub-agent. The main agent does not hold the chapter body; it reads the file with the Read tool only when needed.

**G-1. Sub-agent invocation template:**

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

Corresponding real sources (Read these):
- app/models/project.rb
- app/models/issue.rb
- app/models/user.rb
- app/models/role.rb
- db/schema.rb (relevant portions)

Draft output path: .specback/drafts/05-data-model.md

Quality bar:
- Body ≥ 200 lines
- <!-- REF: SRC-NNNN --> ≥ 10
- fenced code blocks ≥ 3
- Mermaid diagrams ≥ 1 (ER diagram)
- ≥ 5 files under ## Sources Read

[mode B IMPORTANT] In the task return text, include **only the path and a
summary** — do NOT paste the chapter body. Save the detail questions to
the trailing <!-- DETAIL_QUESTIONS --> comment in
drafts/05-data-model.md, and list only the top 5 in the return value.

NOTE: If goal.output_language == "ja", render the chapter body, headings,
prose, and detail-question text in Japanese. Keep code blocks, file
paths, JSON keys, <!-- REF: ... --> markers, and the literal heading
"## Sources Read" in English.
""",
  subagent_type="chapter-investigator"
)
```

**G-2. Processing the sub-agent's return value (main agent's responsibility):**

The sub-agent returns 4 blocks; process them in order:

1. **Key findings** — surface in the conversation to share the chapter's summary.
2. **Detail questions raised** — append the top 5 entries to `questions.json`.
3. **Manifest line** — **append** one line to `{output_dir}/.specback/state/manifest.md` (see G-3).
4. Then **proceed to the next chapter without reading the body**. Open `drafts/NN-slug.md` with the Read tool only when Phase 4 verification or cross-chapter consistency requires it.

**G-3. Manifest update:**

After every per-chapter `task` completes, append a row to `{output_dir}/.specback/state/manifest.md`. If the file does not exist, create it with the Write tool and the header:

```markdown
# specback Drafts Manifest

| NN | slug | path | inventory_ids | lines | key topic |
|----|------|------|----------------|------|----------------|
| 05 | data-model | .specback/drafts/05-data-model.md | INV-012,INV-013,INV-014,INV-015 | 234 | Project / Issue / User / Role relationships |
```

This manifest is the **single entry point** when the main agent needs a chapter-level overview in Phase 4 / 5 / 6. Because the chapter body never enters the conversation history, the main agent first reads the manifest and only then opens the specific chapter via the Read tool.

**G-4. Important constraints:**

- **Sequential execution (parallelism 1)**: Each invocation of the Task tool dispatches a fresh sub-agent in an isolated context. The main agent waits for each sub-agent before issuing the next; total time is similar to in-process processing, but the isolated per-chapter contexts improve quality.
- **Prompt cache is NOT shared**: each sub-agent has an isolated LLM context, so token usage is 5–10× the main agent. **The sub-agent writes the chapter draft directly via the Write tool** (saved as a file, NOT returned in the task result text). The main agent only reads the 4 return blocks.
- **Invoke once per chapter**. Bundling all chapters into one `task` call defeats the purpose (isolated contexts disappear).
- **If the return value contains the chapter body**, re-run that chapter's task (re-emphasise in the prompt: "return value contains only the path and the summary — do NOT paste the body").

**When the `Task` tool is unavailable or chapter delegation is not desired**, the main agent performs STEP A-F per chapter itself.

