# Verification Checklists Reference

A catalogue of checklists run in Phase 4 (Verify). Alongside inventory-based verification, this defines per-template mandatory-item checks and cross-cutting quality checks.

---

## Verification has 3 tiers

Phase 4 verification runs in 3 tiers:

1. **Inventory-based check**: whether code-derived units are mentioned in the spec (`scripts/coverage-check.py`).
2. **Template mandatory-item check**: whether the selected template's required chapters/sections are satisfied (this document).
3. **Cross-cutting quality check**: amount of uncertainty markers, presence of traceability citations, cross-chapter consistency (this document).

---

## Per-template mandatory-item checks

### Web application spec

- [ ] The overview chapter lists "3 to 5 main use cases".
- [ ] Every endpoint / route is listed in the "Routes" chapter.
- [ ] Each screen documents its "access conditions (auth required, role)".
- [ ] The data-model chapter has an ER diagram or an entity list.
- [ ] The authentication chapter covers "auth method", "role model", and "session management".
- [ ] The integration chapter documents each partner's "integration method" and "behaviour on failure".
- [ ] The operations chapter documents "environment variables" and "deployment procedure".

### Batch-system spec

- [ ] Every job is listed in the "Job catalogue" chapter.
- [ ] Each job documents "trigger conditions", "expected runtime", and "execution user".
- [ ] The data-flow chapter documents "input data source" and "output destination".
- [ ] The error-handling chapter documents "retry policy" and "behaviour on failure".
- [ ] The recovery chapter documents "re-runnability" and "idempotency".
- [ ] The operations-calendar chapter contains a job-dependency graph.
- [ ] The monitoring chapter documents "monitored items" and "alert conditions".

### API service spec

- [ ] Every endpoint is described in its own section.
- [ ] Each endpoint has "method", "URL pattern", "auth required", "request example", "response example".
- [ ] The error-codes chapter enumerates every error code.
- [ ] The authentication chapter documents "method", "token lifetime", "refresh procedure".
- [ ] The rate-limit chapter documents "limit value", "limit unit", "behaviour when exceeded".
- [ ] The versioning chapter documents "current version", "support window", "breaking-change policy".
- [ ] The SLA chapter documents "availability target" and "response-time target".

### Library / SDK spec

- [ ] The installation chapter contains per-package-manager command examples.
- [ ] The public-API catalogue lists every public class / function.
- [ ] The usage-examples chapter contains a "minimal quick start".
- [ ] The configuration-options chapter documents every option with type, default, and meaning.
- [ ] The compatibility chapter documents "supported language versions" and "dependencies".
- [ ] The migration-guide chapter is prepared (kept as an empty section in v1 is fine).

---

## Cross-cutting quality checks

Regardless of template, confirm the following on every spec.

### Filename convention and required files
- [ ] Every chapter file under `{output_dir}/.specback/drafts/` matches `^(0\\d|[1-9]\\d)-[a-z0-9-]+\\.md$` (`coverage-check.py` checks this; violations are WARN).
- [ ] The three required files (`00-metadata.md`, `99-unresolved.md`, `traceability.md`) exist under `{output_dir}/.specback/drafts/` or `{output_dir}/` (default: `{output_dir}/`) (`coverage-check.py` checks this; missing files are ERROR).
- [ ] Chapter numbers (`NN`) have no duplicates and no unnecessary gaps.

### Traceability
- [ ] Every chapter has at least one `<!-- REF: ... -->` citation.
- [ ] The traceability table is generated as `final/traceability.md`.
- [ ] Cited file paths exist (no broken links).
- [ ] Cited line ranges are valid (do not exceed the file's line count).

### Uncertainty markers
- [ ] When `<!-- BLOCKED -->` markers appear, the corresponding Question ID exists in `questions.json`.
- [ ] `<!-- CONFIDENCE: LOW -->` markers do not appear in more than half of any chapter (if they do, consider re-running the sub-agent).
- [ ] Remaining `<!-- ASK SME -->` markers are recorded as items to be handled in Phase 5 dialogue.

### Cross-chapter consistency
- [ ] When the same inventory item is mentioned in multiple chapters, the descriptions do not contradict each other.
- [ ] Configuration values / thresholds are consistent across chapters (e.g. retry count).
- [ ] Terminology is consistent across chapters (e.g. no mixing of "user" and "member").

### Question Bank consistency
- [ ] Every `questions.json` entry has all required fields.
- [ ] `status: open` entries have a `severity`.
- [ ] `status: answered` entries have `answer` and `answered_at` set.
- [ ] Inventory IDs referenced in `related_inventory_ids` exist in `inventory.json`.

### "Unresolved items" chapter
- [ ] Every `status: abandoned` entry is captured in the "Unresolved items" chapter.
- [ ] Each unresolved item documents "why it could not be resolved", "current inference", "what is needed to resolve in the future".

---

## Mapping to the verification script

Items automated by `scripts/coverage-check.py`:
- Inventory-based check (all items)
- Chapter naming-convention check (WARN; promoted to ERROR with `--strict`)
- Presence of the three required files (`00-metadata.md` / `99-unresolved.md` / `traceability.md`; always ERROR if missing)
- Existence of cited file paths in traceability
- Required-field check on the Question Bank

Items left to human or Claude review:
- Per-template mandatory items (semantic checks)
- Cross-chapter consistency (semantic)
- Terminology consistency

---

## Example verification report

At the end of Phase 4, report to the user in the following format.

```
=== Phase 4 Verification Report ===

[Inventory coverage]
- All inventory items: 247
- Mentioned: 232 (93.9%)
- Unmentioned: 15
  - INV-042 ScheduledMaintenanceJob (src/jobs/ScheduledMaintenanceJob.php:8)
  - INV-067 LegacyApiAdapter (src/adapters/LegacyApiAdapter.php:15)
  - ...

[Template mandatory items]
- Web application spec mandatory items: 7
- Satisfied: 5
- Unsatisfied: 2
  - "Integration chapter documents each partner's behaviour on failure"
  - "Operations chapter documents the deployment procedure"

[Cross-cutting quality]
- Traceability citations: 43 chapters; average 8.2 / chapter ✓
- [BLOCKED] markers: 3 (corresponding Question IDs: Q-014, Q-027, Q-038)
- <!-- CONFIDENCE: LOW --> markers: 12
- Cross-chapter consistency: 2 contradictions detected
  - "Retry count" is described as 3 in Chapter 3 and 5 in Chapter 5 → filed as Q-098
  - ...

[Question Bank]
- All questions: 84
- open: 42
- Duplicate candidates (auto-merge): 6
- Grouping candidates (user decision): 8

[Recommended next actions]
- Generate additional-investigation tasks for the 15 unmentioned inventory items
- File the 2 cross-chapter inconsistencies into the Question Bank
- Proceed to Phase 5 (Refine via Dialogue)
```

---

## Customising the checklist

When the user wants to add their own checks, create `{output_dir}/.specback/custom-checklists.md` and append items there. In Phase 4, Claude runs the custom checks in addition to the standard ones.

Format custom checks like this:

```markdown
- [ ] [check_id] description of the check
  - Applicability: template type / all templates
  - Method: manual / automatic (script path)
  - On failure: warning only / defer to Phase 5
```
