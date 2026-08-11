# Token Estimate & Budget Gate

> **Documentation**: [English](07-token-estimate.md) · [日本語](07-token-estimate.md)

## Overview

Phase 3's parallel sub-agent investigation consumes tokens proportional to the
codebase size — tens of thousands to hundreds of thousands of tokens on large
projects. Without an estimate **before** the run, the user cannot decide
whether to proceed (the cost-benefit principle: "if I don't know what it
costs, I can't decide to run it").

`specback-estimate.py` (Issue #267) prints an **estimated token consumption**
for Phase 3 from the Phase 2 outputs, before Phase 3 starts:

- `.specback/inventory.json` — unit count (list or `{"units": [...]}` form)
- `.specback/goal.json` — `depth_mode` / `tone`
- `.specback/wbs.json` — chapter count

## Usage

```bash
python scripts/specback-estimate.py --specback-dir .specback
# Estimated tokens: 123,456
# Chapters: 8 | Units: 1,200 | depth_mode: comprehensive | tone: thorough

# Machine-readable
python scripts/specback-estimate.py --specback-dir .specback --json

# Budget gate: exit code 2 when the estimate exceeds the limit
python scripts/specback-estimate.py --specback-dir .specback --budget-limit 50000

# Record actual consumption after a run (for calibration)
python scripts/specback-estimate.py --specback-dir .specback --record-actual 145000
```

## Estimation formula

```text
raw = BASE_TOKENS_PER_CHAPTER × num_chapters + TOKENS_PER_UNIT × num_units
raw = raw × DEPTH_MODE_FACTOR[depth_mode] × TONE_FACTOR[tone]
```

Initial constants (module-level, calibrated over time):

| Constant | Value |
|----------|-------|
| `BASE_TOKENS_PER_CHAPTER` | 2000 |
| `TOKENS_PER_UNIT` | 300 |
| `DEPTH_MODE_FACTOR` | comprehensive 1.0 / interactive 0.8 / outline 0.5 |
| `TONE_FACTOR` | thorough 1.0 / concise 0.7 |

The estimate is **model-independent** — a deliberate decision: tokenizer
differences between models (±10–20%) are within the ±50% tolerance of the
success metric, and a per-model price table would need maintenance on every
new model release. Prices are deliberately **not** printed.

## Calibration

After each run, record the actual token consumption:

```bash
python scripts/specback-estimate.py --specback-dir .specback --record-actual <actual_tokens>
```

This appends `{timestamp, depth_mode, tone, num_chapters, num_units,
estimated_tokens, actual_tokens}` to `.specback/estimate-history.json`. Once 3+
actuals exist, the CLI calibrates future estimates by the median
`actual / estimated` ratio.

### History hardening

- `--record-actual` / `--budget-limit` only accept **positive integers** —
  zero / negative values are rejected (exit 2), so calibration data cannot be
  silently poisoned.
- `estimate-history.json` is written **atomically** (temp file + rename) and
  **refuses to write through a symlink** — a malicious repo cannot overwrite
  an arbitrary file via `--record-actual`.
- Non-finite JSON constants (`NaN` / `Infinity`) and broken JSON in the
  history are rejected; the corrupt file is quarantined to
  `estimate-history.json.bak` and estimation continues fresh.
- History is capped at the last 50 runs so old anomalies fade out, and only
  entries with positive finite `estimated_tokens` / `actual_tokens`
  contribute to the calibration ratio.

## Integration point

Phase 2 → Phase 3 boundary: `skills/specback/phases/phase-2-wbs.md` step 6.5
("Token estimate & budget gate"). If the estimate exceeds the budget, the
script suggests switching `depth_mode` to `outline` (roughly half the tokens
of comprehensive) and re-running.
