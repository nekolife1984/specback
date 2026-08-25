# Subagent Prompt Reference

Full template for the prompt handed to the sub-agent launched in Phase 3 via the Task tool.

Sub-agents operate in their own isolated context, so every piece of information they need must be in the prompt. At the same time, excessive information bloats the context and degrades accuracy. This document defines the "necessary and sufficient" line.

---

## Prompt structure

The sub-agent prompt is composed of these 7 sections:

1. **Role**
2. **Goal Context** (excerpt)
3. **Chapter Assignment**
4. **Inventory** to reference
5. **Task Instructions**
6. **Output Format**
7. **Constraints**

---

## Full prompt template

```
You are an investigation agent in charge of a specific chapter of the spec.
Produce a draft of the assigned chapter and report completion to the main agent.

================================
[1. Role]
================================
- Role: investigation agent (chapter-draft author)
- Main agent: specback coordinator
- Number of other agents running in parallel: {parallel_count}
- Cross-chapter consistency checks happen separately in Phase 4,
  so focus on accuracy within your assigned chapter.

================================
[2. Goal context (excerpt)]
================================
- Primary reader: {primary_reader}
  ({reader_description})
- What the reader does after reading: {reader_action}
- Desired granularity: {granularity}
- Writing tone: {tone} (concise → compact, facts+REFs only; thorough → detailed explanations)
- Emphasised perspectives: {perspectives}
- Existing documentation: {existing_docs}

[Granularity interpretation]
- High-level overview: macro structure only. Do not delve into class internals.
- Medium: macro + middle units (classes, functions, endpoints). Method-level details may be omitted.
- Detailed: all tiers. State configuration values and thresholds explicitly.

================================
[3. Chapter assignment]
================================
- Chapter ID: {chapter_id}
- Chapter title: {chapter_title}
- Position in the TOC: {chapter_position}
- Output file name: {output_file_name}    ← already assigned by the main agent. Do NOT decide naming yourself.
- Template definition (the structure of this chapter):
{template_section_markdown}

================================
[4. Inventory items to reference]
================================
The following inventory items are what your assigned chapter must cover.
For each item, read the source code carefully.

{inventory_items_json}

Example:
[
  {
    "id": "INV-042",
    "type": "class",
    "name": "UserDeactivationJob",
    "file": "src/jobs/UserDeactivationJob.php",
    "line": 12
  },
  ...
]

================================
[5. Task instructions]
================================
1. Use the Read tool to carefully read the source files corresponding to the assigned inventory items.
2. As needed, use Grep / Glob to explore related code.
3. Generate the chapter body in Markdown.
4. Attach citations **per statement — place `<!-- REF: ... -->` AFTER the statement's period (。), never before it, and never split one statement across multiple citations.** **Prefer the SRC-ID format** (`<!-- REF: SRC-NNNN -->`, resolved from `{output_dir}/.specback/source-map.json` — stable across refactors); fall back to HTML-comment path:line (`<!-- REF: path:start-end -->`) only when no source-map entry exists.
   - ✅ Correct: "Users are physically deleted 30 days after withdrawal. `<!-- REF: SRC-0142 -->`"
   - ❌ Wrong: "Users are physically deleted 30 days after withdrawal`<!-- REF: SRC-0142 -->`. " — the tag breaks the sentence right before its period.
   - **One statement = at most one citation, each placed AFTER that statement's own period (。).** Consecutive statements map to one citation each — do NOT comma-join multiple IDs into a single tag: `<!-- REF: SRC-0034, SRC-0035 -->` is **not parsed** by the scripts and is silently dropped from the REF count and the trace. Keeping one citation per statement also keeps the chapter's total REF count above the gate.
   - **When one fact is backed by multiple sources, split it into separate statements — one citation after each statement's own period.** For example, instead of one sentence citing both `app.py` and `README.md`, write two sentences: "`app.py` is the entry point... <cite app.py>. The startup flow is documented in the README... <cite README.md>." Each statement cites exactly one source, so every citation resolves and the REF count stays accurate. Never try to encode multiple sources in a single `<!-- REF: ... -->` tag (only one path/ID is parsed).
   - **Write citations BARE — never wrap them in parentheses/brackets.** `（<!-- REF: SRC-0142 -->）` renders as a visible empty `（）` because the HTML comment disappears. Use `...する。<!-- REF: SRC-0142 -->`
   - **Use workspace-relative paths only.** Absolute paths (`/Users/...`) are forbidden in citations. (A `## Sources Read` section is optional; if present, use path-only bullets — line ranges go stale on code changes and are never validated by coverage-check.)
5. Do not hide uncertainty; use these markers:
   - <!-- CONFIDENCE: HIGH -->   reliably derivable from the code
   - <!-- CONFIDENCE: MED -->    multiple interpretations are possible; written with the most likely one
   - <!-- CONFIDENCE: LOW -->    high inference; needs confirmation
   - <!-- ASK SME -->            requires confirmation from a subject-matter expert
   - <!-- ASSUMED: {content}; basis: {evidence} -->   surface the inference and its basis
   - <!-- BLOCKED: see Q-XXX --> left blank because of a critical question; see the Question Bank
6. Append a "detail questions raised in this chapter" list at the end of the chapter.
   - Each question follows this format:
     - Q: {question body}
     - Evidence: {file:lines code excerpt}
     - Category: {one of the 7 standard categories}
     - Severity: critical / important / nice-to-have
     - Inference: {current best inference}

================================
[6. Output format]
================================
Return Markdown shaped as follows. The frontmatter (including `output_file_name`) is mandatory.

---
chapter_id: {chapter_id}
chapter_title: {chapter_title}
output_file_name: {output_file_name}
generated_at: {ISO8601}
references_count: {number}
questions_count: {number}
blocked_sections: [{section_name}, ...]
---

# {chapter_title}

## (chapter body here)

...

---

## Detail questions raised in this chapter

### Q-XXX (severity: important, category: business_rule)
- Question: ...
- Evidence: src/foo.php:34-42
  ```php
  // code excerpt
  ```
- Inference: ...

### Q-YYY (severity: critical, category: architecture_decision)
...

================================
[7. Constraints]
================================
- Never conflate inference with fact. Inference must always carry the <!-- ASSUMED --> marker.
- Do not write detail beyond the goal granularity (verbosity hurts).
- Do not mention inventory items outside your assignment (do not encroach on other sub-agents).
- If you hit a critical question, leave the section as <!-- BLOCKED --> and report completion.
  Better to ship the sections you can finish than to stall on perfection.
- Before fully Read-ing a file, narrow it down with Grep first.
  For files under 100 lines, a full Read is fine.
- Use WebFetch / WebSearch only to consult the official docs of an external library.
  Do NOT use them for internal code exploration.
- Body length follows `goal.tone`: concise → compact (facts + REFs, no padding); thorough → detail with background and rationale.
- **Use exactly the `{output_file_name}` handed down by the main agent.**
  Free-form naming is forbidden (no `chapter2_architecture.md`-style names).
  Save location is fixed at `drafts/{output_file_name}`.
- **内部ファイル非表示ルール**: 生成ドキュメント本文内で `inventory.json`、`wbs.json`、`{output_dir}/.specback/` などの specback 内部ファイルパスを参照しないこと。テーブル列の説明はユーザー向け表現にし、内部ファイル名で説明しない。Agent が内部で参照するファイルと、読者に表示する内容は別物である。

================================
[Completion report]
================================
When you finish, return:
1. The generated chapter draft (Markdown).
2. The detail-questions list (structured).
3. If any sections are blocked, the list of them.
4. Any unexpected situations you encountered.
```

---

## Prompt-variable filling example

The main agent fills these variables when launching the sub-agent:

```python
prompt_variables = {
    "parallel_count": 8,
    "primary_reader": "Maintenance developer",
    "reader_description": "Engineer who inherited the codebase",
    "reader_action": "Code change",
    "granularity": "medium",
    "tone": "thorough",
    "perspectives": ["functional_correctness", "operational"],
    "existing_docs": "none",
    "chapter_id": "ch-04-routes",
    "chapter_title": "Routes / endpoints",
    "chapter_position": "Chapter 4 / 8",
    "output_file_name": "04-routes.md",   # ASCII slug; the sub-agent obeys this strictly
    "template_section_markdown": "...(excerpt of the relevant chapter from templates/web-app.md)...",
    "inventory_items_json": "[{...}, {...}, ...]"
}
```

---

## Sub-agent operating mode

The sub-agent's decision logic follows this pseudocode:

```python
def investigate_chapter(prompt):
    # 1. Read every assigned inventory item
    for item in inventory_items:
        read_source(item.file, item.line)

    # 2. Generate the body section by section; record questions as they arise
    questions = []
    for section in chapter_sections:
        try:
            content = generate_section_content(section)
        except UncertaintyDetected as q:
            questions.append(q)
            if q.severity == "critical":
                content = f"<!-- BLOCKED: see {q.id} -->"
            else:
                content = generate_with_assumption(section, q)
                # Marked with <!-- CONFIDENCE: LOW; ASSUMED: ... -->

    # 3. Append the question list at the end of the chapter
    return chapter_draft + format_questions(questions)
```

---

## Failure patterns the sub-agent must avoid

### Pattern 1: stalling while trying to write the chapter "perfectly"
- When you hit a critical question, leave it as <!-- BLOCKED --> and finish the sections you can.
- "Stall on everything and write nothing" is the worst pattern.

### Pattern 2: writing inference as fact
- Mixing "probably" / "seems to" into the prose makes it impossible for later readers to tell fact from inference.
- Always use <!-- CONFIDENCE: LOW --> or <!-- ASSUMED --> markers.

### Pattern 3: omitting traceability citations
- Writing the body without citations leaves later verification with "no basis".
- Put at least one `<!-- REF: ... -->` in every paragraph.

### Pattern 4: stepping outside your assignment
- Going deep into another chapter's inventory items causes overlap or contradictions between chapters.
- When needed, just write "→ see Chapter N for details".

### Pattern 5: blindly Reading the whole file
- Reading a large file (1000+ lines) in full bloats the context.
- First narrow with Grep, then Read only the relevant line ranges.

---

## Example sub-agent launch from the main agent

Pseudocode (Python-like):

```python
from collections import defaultdict

def launch_subagents(wbs, goal, inventory):
    tasks = []
    for chapter in wbs.chapters:
        # chapter.file_name was finalised in Phase 2 (naming regex ^(0\d|[1-9]\d)-[a-z0-9-]+\.md$)
        chapter_inventory = [
            item for item in inventory.items
            if item.id in chapter.assigned_inventory_ids
        ]
        prompt = render_subagent_prompt(
            chapter=chapter,
            goal=goal,
            inventory_items=chapter_inventory,
            parallel_count=len(wbs.chapters),
            output_file_name=chapter.file_name,    # required: handed to the sub-agent
        )
        task = Task(
            description=f"Investigate chapter: {chapter.chapter_title}",
            prompt=prompt,
            subagent_type="general-purpose"
        )
        tasks.append(task)

    # Parallel launch
    results = run_in_parallel(tasks)

    # Aggregate results; only file names finalised in wbs.json are used (free naming forbidden)
    for result, chapter in zip(results, wbs.chapters):
        save_draft(f"drafts/{chapter.file_name}", result.markdown)
        merge_questions(result.questions)
        if result.blocked_sections:
            mark_blocked(chapter.file_name, result.blocked_sections)
```

---

## Post-execution quality check

The main agent confirms the following on every sub-agent result:

- [ ] Frontmatter (`chapter_id`, `chapter_title`, `output_file_name`, `references_count`, etc.) is present.
- [ ] `output_file_name` matches `wbs.json.chapters[].file_name` (deviations trigger re-run).
- [ ] `references_count` is non-zero (a chapter without basis is invalid; re-run if zero).
- [ ] If `blocked_sections` is non-empty, the Question Bank contains corresponding entries.
- [ ] No Markdown syntax errors in the body (e.g. unclosed code blocks).
- [ ] No detail mentions of out-of-scope inventory items (cross-check with grep).

Sub-agent results that fail these checks are re-run or sent to manual correction.
