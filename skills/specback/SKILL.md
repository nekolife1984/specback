---
name: specback
description: Reverse-engineer comprehensive specification documents from existing codebases. Uses the Agent-driven workflow (phase prompts executed by a coding agent).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task, AskUserQuestion, WebFetch, WebSearch
metadata:
  short-description: >-
    Reverse-spec generator for legacy codebases.
    This skill is the REFERENCE IMPLEMENTATION.
    The active workflow is Agent-driven (phases/ prompts executed by a coding agent).
---
# specback — Agent-Driven Workflow

## Quick start

### Agent-driven (this skill)

1. Install the skill to your agent:
   ```bash
   ./install.sh --agent claude --level project
   ```
2. Navigate to your target codebase
3. Invoke the skill (`/specback` or equivalent)
4. Follow the agent's prompts through each phase

## Phase reference

The phase documents describe the agent-driven workflow in detail.
Each phase is a standalone prompt sequence you can run with your coding agent.

| Phase | Name | Archive file | Envelope |
|-------|------|------------|----------|
| 0 | Setup & Goal | `phases/phase-0-setup.md` | `GoalOutput` |
| 1 | Recon & Template | `phases/phase-1-recon.md` | `ReconOutput` |
| 2 | Plan & WBS | `phases/phase-2-wbs.md` | `WBSOutput` |
| 3 | Investigate | `phases/phase-3-investigate.md` | `InvestigateOutput` |
| 4 | Verify | `phases/phase-4-verify.md` | `VerifyOutput` |
| 5 | Refine via Dialogue | `phases/phase-5-dialogue.md` | `DialogueOutput` |
| 6 | Deliver | `phases/phase-6-deliver.md` | `DeliverOutput` |
| 6.5 | Interactive Deep-Dive | `phases/phase-6-5-deepdive.md` | (interactive) |
| 7 | Drift Detection | `phases/phase-7-drift.md` | `DriftOutput` |
| 7b | REF Auto-Fix | `phases/phase-7b-ref-autofix.md` | (code) |
| 7c | ChangeSpec | `phases/phase-7c-changespec.md` | `ChangeSpecOutput` |
| 7d | Config Refresh | `phases/phase-7d-config-refresh.md` | (code) |
| 7e | Incremental Update | `phases/phase-7e-incremental-update.md` | (code) |

## Supporting files (shared)

These files are used by this reference skill.
All paths are relative to the repo root.

| Path | Contents |
|------|----------|
| `scripts/gates.py` | GateReport + 4 gates (coverage_mece, schema_valid, traceability_full, drift_detected) |
| `scripts/data_types.py` | Typed envelopes for phase output |
| `references/gates.md` | Gate reference (EN) |
| `references/gates.ja.md` | Gate reference (JA) |
| `references/data_types.md` | Typed envelopes reference |
| `references/template-catalog.md` | Template catalog |
| `references/inventory-units.md` | Inventory units per language |
| `references/verification-checklists.md` | Verification checklists |
| `references/drift-detection.md` | Drift detection protocol |
| `references/source-map-update.md` | Safe source-map update when adding files (avoid SRC-ID renumber trap) |
| `references/change-specification.md` | ChangeSpec protocol |
| `references/outline-tables.md` | Outline table patterns |
| `references/question-categories.md` | Question Bank categories |
| `references/subagent-prompt.md` | Sub-agent prompt template |
| `schemas/goal.schema.json` | Goal JSON schema |
| `schemas/questions.schema.json` | Questions JSON schema |
| `schemas/state.schema.json` | State JSON schema |
| `docs/skill-behavior/question-bank.md` | Question Bank data structure |
| `docs/skill-behavior/subagent-behavior.md` | Sub-agent behavior docs |
| `docs/skill-behavior/state-management.md` | State management docs |
| `docs/skill-behavior/doubt-pass.md` | Doubt-pass protocol |
