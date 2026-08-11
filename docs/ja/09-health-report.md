# 仕様ヘルスレポート（specback-health）

> **ドキュメント**: [English](../en/09-health-report.md) · [日本語](09-health-report.md)

## 概要

生成された仕様書の信頼性は、これまで最終成果物を見てからでないと判断できませんでした。🔴 ASSUMED 密度・REF 密度・未解決項目数・ドリフト状態が章ごとに散在し、俯瞰できないため、「この仕様書は全体としてどの程度信頼できるか」を1枚で示す手段がありませんでした。

`specback-health.py`（Issue #268）は、既存出力を集約して**章別・全体の「仕様ヘルススコア」を1枚の Markdown スコアカード**として出力します:

| 入力 | 用途 |
|------|------|
| `drafts/*.md` / 最終出力 `*.md` | 章別の確度ラベル（🟢/🟡/🔴）・REF 数・未解決項目をコードフェンス外から集計 |
| `questions.json` | 未解決質問の割合 |
| `trace.json` | MECE カバレッジ |
| `drift-report.json` | ドリフト状態（変更ファイル数・影響セクション数。`--output-dir` を優先し、次に `--specback-dir` を確認） |
| `state.json` | 現在フェーズ |
| `coverage-check.py --output-format json` | カバレッジ率・ゲート失敗（実行失敗時は N/A として動作継続） |

## 使い方

```bash
python scripts/specback-health.py --specback-dir .specback
# → .specback/health-report.md

# 機械可読出力も生成
python scripts/specback-health.py --specback-dir .specback --json
# → .specback/health-report.md + .specback/health-report.json

# Phase 6 ゲート: スコアが 70 未満なら exit code 2
python scripts/specback-health.py --specback-dir .specback --min-health-score 70

# 🔴 密度の警告閾値を変更（デフォルト 0.3 = 30%）
python scripts/specback-health.py --specback-dir .specback --assumed-ratio-threshold 0.25
```

exit codes:
- `0` — 正常（ゲートなし、またはスコアが閾値以上）
- `1` — `--specback-dir` が存在しない、章ファイルが1つも見つからない、またはゲート値が不正
- `2` — 全体スコアが `--min-health-score` 未満

章スキャンの注意:
- 章は `{specback-dir}/drafts/` を優先して読み、同名衝突時は **draft 版が優先**されます（drafts が最新の作業コピー）。`--output-dir` が `--specback-dir` と異なる場合は、出力先の最終版もスキャンします。
- 予約ファイル（`00-metadata.md` / `99-unresolved.md` / `traceability.md`）とレポート自身の前回出力（`health-report.md`）は除外されます — これらは章ではないため、スコアカードを歪めます。

## ヘルススコアの計算

### 章別スコア（0–100）

```text
total_labels = verified + inferred + assumed
assumed_ratio = assumed / total_labels          # ラベル0件なら 0.0
ref_density  = refs / max(body_lines, 1)

score = 100
      - round(assumed_ratio × 50)               # 🔴 が多いほど減点
      - min(unresolved, 10) × 5                 # 未解決項目で減点
      - 20 (body_lines < 10 の場合)              # 薄い章は減点
      + min(ref_density × 200, 10)              # REF 密度が高いほど加点
clamp(0, 100)
```

### 全体スコア（0–100）— 利用可能なメトリクスの加重平均

| メトリクス | 重み |
|-----------|------|
| カバレッジ率（coverage-check） | 0.30 |
| MECE カバレッジ（trace.json） | 0.20 |
| 全体 ASSUMED 率の逆数 | 0.20 |
| 未解決質問率の逆数 | 0.15 |
| 章別スコアの平均 | 0.15 |

欠損メトリクスは重みを残りに比例配分して正規化します（例: coverage-check が実行できなくても残りで計算）。全メトリクス欠損時は 0。

### レーティング

| スコア | レーティング | 意味 |
|--------|-------------|------|
| ≥ 90 | A | 納品可能 |
| 75–89 | B | 軽微な精緻化を推奨 |
| 60–74 | C | 精緻化が必要 |
| < 60 | D | 要再調査 |

## 出力例

```markdown
Overall health score: **72 / 100 (C: 精緻化が必要)**

## Per-chapter scorecard
| Chapter | Score | Body lines | REFs | 🟢 | 🟡 | 🔴 | Unresolved | ASSUMED % | Flags |
|---------|-------|-----------|------|----|----|----|------------|-----------|-------|
| 01-overview.md | 85 | 120 | 8 | 5 | 2 | 1 | 0 | 12% | ✅ |

## Needs refinement (Phase 5 suggested)
- 03-data-model.md: ASSUMED ratio 45% (threshold 30%) — strengthen grounding via mechanical extraction
```

`--assumed-ratio-threshold`（デフォルト 0.3）を超える章は「Needs refinement」にリストされ、Phase 5 の対話精緻化へ誘導されます。

## 組み込みポイント

**Phase 6 納品前の必須チェック**: `skills/specback/phases/phase-6-deliver.md` のステップ 6（Intent-vs-delivery audit）にヘルスレポート生成が組み込まれています。`--min-health-score 70` を付けて実行し、スコア 70 未満の場合は該当章を Phase 5 に戻して精緻化します（成功指標: 精緻化後の 🔴 ASSUMED 密度が 30% 以上低下）。
