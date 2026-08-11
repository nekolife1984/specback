# Spec Health Report (specback-health)

> **Documentation**: [English](09-health-report.md) · [日本語](../ja/09-health-report.md)

## Overview

Until now, the reliability of a generated spec could only be judged after
looking at the final deliverable. 🔴 ASSUMED density, REF density, unresolved
items, and drift state are scattered across chapters, so there was no single
view of "how trustworthy is this spec overall".

`specback-health.py` (Issue #268) aggregates existing outputs into a
**per-chapter and overall "spec health score" rendered as a single Markdown
scorecard**:

| Input | Used for |
|-------|----------|
| `drafts/*.md` / final output `*.md` | Per-chapter confidence labels (🟢/🟡/🔴), REF count, unresolved items (counted outside code fences) |
| `questions.json` | Open-question ratio |
| `trace.json` | MECE coverage |
| `drift-report.json` | Drift state (changed files, affected sections; read from `--output-dir` first, then `--specback-dir`) |
| `state.json` | Current phase |
| `coverage-check.py --output-format json` | Coverage rate, gate failures (falls back to N/A on failure) |

## Usage

```bash
python scripts/specback-health.py --specback-dir .specback
# → .specback/health-report.md

# Also emit machine-readable output
python scripts/specback-health.py --specback-dir .specback --json
# → .specback/health-report.md + .specback/health-report.json

# Phase 6 gate: exit code 2 when the score is below 70
python scripts/specback-health.py --specback-dir .specback --min-health-score 70

# Change the 🔴 density warning threshold (default 0.3 = 30%)
python scripts/specback-health.py --specback-dir .specback --assumed-ratio-threshold 0.25
```

Exit codes:
- `0` — OK (no gate, or score at/above threshold)
- `1` — `--specback-dir` missing, no chapter files found, or invalid gate values
- `2` — overall score below `--min-health-score`

Chapter scanning notes:
- Chapters are read from `{specback-dir}/drafts/` first; on a name collision
  the **draft** version wins (drafts are the newest working copies). When
  `--output-dir` differs from `--specback-dir`, final copies under the output
  dir are scanned too.
- The reserved files (`00-metadata.md` / `99-unresolved.md` /
  `traceability.md`) and the report's own previous output
  (`health-report.md`) are excluded — they are not spec chapters and would
  skew the scorecard.

## Health score calculation

### Per-chapter score (0–100)

```text
total_labels = verified + inferred + assumed
assumed_ratio = assumed / total_labels          # 0.0 when no labels
ref_density  = refs / max(body_lines, 1)

score = 100
      - round(assumed_ratio × 50)               # 🔴 density penalty
      - min(unresolved, 10) × 5                 # unresolved items penalty
      - 20 (when body_lines < 10)               # thin chapter penalty
      + min(ref_density × 200, 10)              # REF density bonus
clamp(0, 100)
```

### Overall score (0–100) — weighted average of available metrics

| Metric | Weight |
|--------|--------|
| Coverage rate (coverage-check) | 0.30 |
| MECE coverage (trace.json) | 0.20 |
| Inverse of overall ASSUMED ratio | 0.20 |
| Inverse of open-question ratio | 0.15 |
| Mean of per-chapter scores | 0.15 |

Missing metrics renormalize their weight across the remaining ones (e.g. the
report still works when coverage-check cannot run). If every metric is
missing, the score is 0.

### Rating

| Score | Rating | Meaning |
|-------|--------|---------|
| ≥ 90 | A | Ready to deliver |
| 75–89 | B | Minor refinement recommended |
| 60–74 | C | Refinement needed |
| < 60 | D | Re-investigation required |

## Output example

```markdown
Overall health score: **72 / 100 (C: Refinement needed)**

## Per-chapter scorecard
| Chapter | Score | Body lines | REFs | 🟢 | 🟡 | 🔴 | Unresolved | ASSUMED % | Flags |
|---------|-------|-----------|------|----|----|----|------------|-----------|-------|
| 01-overview.md | 85 | 120 | 8 | 5 | 2 | 1 | 0 | 12% | ✅ |

## Needs refinement (Phase 5 suggested)
- 03-data-model.md: ASSUMED ratio 45% (threshold 30%) — strengthen grounding via mechanical extraction
```

Chapters exceeding `--assumed-ratio-threshold` (default 0.3) are listed under
"Needs refinement" and routed to Phase 5 dialogue refinement.

## Integration point

**Mandatory check before Phase 6 delivery**: `skills/specback/phases/phase-6-deliver.md`
step 6 (Intent-vs-delivery audit) includes health report generation. Run it
with `--min-health-score 70`; if the score is below 70, send the offending
chapters back to Phase 5 for refinement (success metric: 🔴 ASSUMED density
drops by 30%+ after refinement).
