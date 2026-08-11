# Incremental Spec Update

> **Documentation**: [English](08-incremental-update.md) · [日本語](08-incremental-update.md)

## Overview

Drift detection (Phase 7) tells you *which* spec sections are affected by source
changes, but updating those chapters was manual: read the drift report, re-read the
changed source files, rewrite the affected chapters, refresh `trace.json`. For a
spec in maintenance mode this loop ran rarely — so the spec slowly rotted.

**Incremental update** (Issue #269) closes that loop. It takes the existing
`drift-report.json`, identifies the **affected chapters only**, generates a
per-chapter re-investigation prompt (the Phase 3 subagent mechanism, chapter-scoped),
then verifies and applies the updated chapter with two hard safety guards:

1. **SRC-ID renumbering trap guard** — every `<!-- REF: SRC-NNNN -->` in the updated
   chapter must still exist in `source-map.json`. A full re-scan that renumbers
   SRC-IDs silently breaks REFs; this guard refuses to ship such a chapter.
2. **Zero collateral edits** — only the target chapter may change. Any other chapter
   file that differs from the `plan` baseline fails verification.

## How it works

```text
detect-drift.py --json
        │
        ▼
drift-report.json
        │
        ▼
specback-incremental-update.py plan
        │   • affected chapters identified from impacted_sections
        │   • per-chapter re-investigation prompt written
        │   • chapter hash baseline snapshot → state.json
        ▼
(agent) re-investigate each affected chapter
        │
        ▼
updated chapter file
        │
        ▼
specback-incremental-update.py verify --updated <file>
        │   • target is one of the affected chapters?
        │   • all REF SRC-IDs exist in source-map.json?
        │   • no other chapter changed vs baseline?
        ▼
specback-incremental-update.py apply --updated <file>
        • backup → atomic replace → build-trace.py refresh
```

## CLI reference

```
python scripts/specback-incremental-update.py <plan|verify|apply> \
    --specback-dir .specback --output-dir specs [--drift-report drift-report.json]
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--specback-dir` | `.specback` | Directory holding `wbs.json`, `source-map.json`, `trace.json` |
| `--output-dir` | `.` | Where final spec chapters live (e.g. `specs/`) |
| `--drift-report` | `{specback-dir}/drift-report.json` | Drift report input |
| `--json` | off | Machine-readable stdout for plan/verify |
| `--skip-trace-refresh` | off | `apply` — skip the `build-trace.py` subprocess |

### plan

Reads `drift-report.json`, collects affected chapter file names from
`changes[].impacted_sections[].file` and `deleted_with_refs[].impacted_sections[].file`.
For each affected chapter it:

1. Looks up the chapter title from `wbs.json`.
2. Collects the SRC-IDs whose covered sections live in that chapter.
3. Collects the changed source files that map to those SRC-IDs.
4. Renders a **chapter re-investigation prompt** to
   `{specback-dir}/incremental/prompts/{chapter_file}.md`.
5. Snapshots the chapter's sha256 into `{specback-dir}/incremental/state.json`
   (the zero-collateral baseline).

Exit codes: `0` ok · `1` input missing/unreadable · `2` no affected chapters.

### verify

`--updated PATH` — the re-investigated chapter file. Checks:

- **Target check**: the basename must be one of the affected chapters recorded by
  `plan` (exit `3` if no `state.json` — run plan first).
- **SRC-ID existence**: every `<!-- REF: SRC-NNNN -->` must exist in
  `source-map.json` (`units[].id`). Missing IDs are renumbering-trap violations.
  `<!-- REF: path:line -->` form is ignored (handled by Phase 7b tooling).
- **Zero collateral edits**: every other chapter file under `{output_dir}` must match
  its baseline hash. The target itself must differ (otherwise a warning is printed).

Exit codes: `0` passed · `1` failed · `3` state.json missing.

### apply

Runs the same checks as `verify`, then:

1. Backs up the current chapter to `{output_dir}/{target}.pre-incremental`.
2. Atomically replaces it (temp file + `os.replace`).
3. Refreshes `trace.json` via `build-trace.py` (skippable with `--skip-trace-refresh`).

Exit codes: `0` ok · `1` check failure or build-trace failure · `3` state.json missing.

## Re-investigation prompt

`plan` writes a self-contained prompt per affected chapter containing: drift context
(generated_at, base), the changed source files affecting the chapter, the SRC-IDs to
re-check, and instructions to keep REFs valid and touch nothing outside the chapter.
The chapter-scoped prompt is the same Phase 3 subagent mechanism, restricted to the
affected chapter so re-investigation cost stays proportional to the drift.

## Safety design (Issue #269)

| Guard | Mechanism |
|-------|-----------|
| SRC-ID renumbering trap | `verify` refuses chapters with `<!-- REF: SRC-NNNN -->` not present in `source-map.json` |
| Zero collateral edits | `plan` snapshots every chapter hash; `verify` fails on any non-target change |
| Atomic apply | backup + temp file + `os.replace`; never truncate the only copy in place |
| Idempotent state | `state.json` regenerated by `plan`; missing state refuses `verify`/`apply` |
| Input safety | 50 MiB cap on JSON inputs; non-dict JSON rejected with exit 1 |

## Usage example

```bash
# 1. Detect drift (Phase 7)
python scripts/detect-drift.py --specback-dir .specback --output-dir specs --json

# 2. Plan the incremental update
python scripts/specback-incremental-update.py plan \
    --specback-dir .specback --output-dir specs --json

# 3. (agent) re-investigate each affected chapter using
#    .specback/incremental/prompts/<chapter>.md → save to
#    .specback/incremental/updated/<chapter>.md

# 4. Verify the update (SRC-ID guard + zero collateral)
python scripts/specback-incremental-update.py verify \
    --specback-dir .specback --output-dir specs \
    --updated .specback/incremental/updated/05-data-model.md

# 5. Apply + refresh trace
python scripts/specback-incremental-update.py apply \
    --specback-dir .specback --output-dir specs \
    --updated .specback/incremental/updated/05-data-model.md
```

## Related

- Phase 7 drift detection: `skills/specback/phases/phase-7-drift.md`
- Drift CI automation: [05-drift-ci.md](05-drift-ci.md)
- SRC-ID safety precedent: `restore-sourcemap-from-trace.py` (idempotency guard)
