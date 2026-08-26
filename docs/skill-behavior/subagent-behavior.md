## Sub-agent behaviour

### Sub-agent prompt template (skeleton)

The prompt skeleton handed to the Task tool in Phase 3. The complete version lives in `references/subagent-prompt.md`.

```
You are an investigation agent in charge of a specific chapter.

[Goal definition (excerpt from goal.json)]
- Output language: {output_language}  ("en" or "ja")
- Primary reader: {primary_reader}
- Granularity: {granularity}
- Perspectives: {perspectives}

[Output-language handling]
- The chapter body, headings, prose explanations, annotations on uncertainty markers, and the chapter-end detail-question list are ALL rendered in {output_language}.
- Machine-readable elements — file names (ASCII slug), `<!-- REF: ... -->`, `<!-- CONFIDENCE: HIGH|MED|LOW -->`, `<!-- ASK SME -->`, `<!-- ASSUMED: ... -->`, `<!-- BLOCKED: see Q-XXX -->`, IDs (`Q-XXX` / `INV-XXX`) — stay English regardless of {output_language}.
- Even when the reference assets (`templates/*.md`, `references/*.md`) are written in Japanese, when {output_language} == "en" you must dynamically translate the chapter heading examples and body samples into semantically equivalent English before writing the chapter body.

[Assigned chapter]
- Chapter title: {chapter_title}
- Inventory items to cover: {inventory_ids}
- Template definition (the structure of this chapter): {template_section}

[Working instructions]
1. Carefully read the source code corresponding to the assigned inventory items.
2. Write the chapter body.
3. Attach a `<!-- REF: ... -->` citation **per statement — place it AFTER the statement's period (。), never before it, at most one per statement** (each placed after that statement's own period; do NOT comma-join multiple IDs into one tag — `<!-- REF: SRC-0034, SRC-0035 -->` is not parsed and is silently dropped from the REF count and trace; when a fact is backed by multiple sources, split it into separate statements, one citation each). When a paragraph ends with several citations, **place the tags adjacent to one another with NO blank line and NO line break between them** (`...。<!-- REF: A --><!-- REF: B -->`). A blank line between tags splits them into separate Markdown paragraphs, adding a visible vertical gap in the rendered preview. **Prefer the SRC-ID format** (`<!-- REF: SRC-NNNN -->`, resolved from `{output_dir}/.specback/source-map.json` — stable across refactors); fall back to HTML-comment path:line (`<!-- REF: path:start-end -->`) only when no source-map entry exists.
4. Do not hide uncertainty; use the following markers:
   - <!-- CONFIDENCE: HIGH | MED | LOW -->
   - <!-- ASK SME -->
   - <!-- ASSUMED: <inference>; basis: <evidence> -->
   - <!-- BLOCKED: section left empty because of a critical question -->
5. At the end of the chapter, append a "detail questions raised in this chapter" list.

[Constraints]
- Never conflate inference with fact.
- Do not write detail beyond the goal granularity.
- When a critical question is hit, leave the section as <!-- BLOCKED --> and report completion.

[Output format]
{See references/subagent-prompt.md for details}
```

### Sub-agent decision logic

When a question is encountered, the sub-agent follows this pseudocode:

```
if question.severity == "critical":
    leave the section as <!-- BLOCKED: see Q-XXX -->
    register the question in the Question Bank
    finish the rest of the chapter as much as possible
    report completion
else:
    leave a <!-- CONFIDENCE: LOW; ASSUMED: <inference> --> marker
    inferred best-effort completion of the chapter
    register the question in the Question Bank
    report completion
```

---
