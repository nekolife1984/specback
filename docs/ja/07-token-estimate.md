# トークン見積り & バジェットゲート

> **ドキュメント**: [English](07-token-estimate.md) · [日本語](07-token-estimate.md)

## 概要

Phase 3 の並列サブエージェント調査はコードベース規模に比例してトークンを消費します（大規模プロジェクトでは数万〜数十万トークン）。実行**前**に見積りが無いと、ユーザーは実行するかどうかを判断できません（コストベネフィット重視の原則: 「いくらかかるか分からなければ実行を決められない」）。

`specback-estimate.py`（Issue #267）は、Phase 3 開始前に Phase 2 の成果物から**推定トークン消費量**を出力します:

- `.specback/inventory.json` — ユニット数（リスト形式または `{"units": [...]}` 形式）
- `.specback/goal.json` — `depth_mode` / `tone`
- `.specback/wbs.json` — 章数

## 使い方

```bash
python scripts/specback-estimate.py --specback-dir .specback
# Estimated tokens: 123,456
# Chapters: 8 | Units: 1,200 | depth_mode: comprehensive | tone: thorough

# 機械可読出力
python scripts/specback-estimate.py --specback-dir .specback --json

# バジェットゲート: 見積りが上限を超えると exit code 2
python scripts/specback-estimate.py --specback-dir .specback --budget-limit 50000

# 実行後に実測を記録（校正用）
python scripts/specback-estimate.py --specback-dir .specback --record-actual 145000
```

## 見積り式

```text
raw = BASE_TOKENS_PER_CHAPTER × 章数 + TOKENS_PER_UNIT × ユニット数
raw = raw × DEPTH_MODE_FACTOR[depth_mode] × TONE_FACTOR[tone]
```

初期定数（モジュールレベル定数。実績で校正）:

| 定数 | 値 |
|------|-----|
| `BASE_TOKENS_PER_CHAPTER` | 2000 |
| `TOKENS_PER_UNIT` | 300 |
| `DEPTH_MODE_FACTOR` | comprehensive 1.0 / interactive 0.8 / outline 0.5 |
| `TONE_FACTOR` | thorough 1.0 / concise 0.7 |

見積りは**モデル非依存**です — これは意図的な設計判断です。モデル間のトークナイザー差（±10〜20%）は成功指標の許容幅 ±50% 内に収まり、モデル別の単価テーブルは新モデルリリースのたびに更新が必要になります。**料金は意図的に出力しません**。

## 校正

実行後に実測トークン消費量を記録します:

```bash
python scripts/specback-estimate.py --specback-dir .specback --record-actual <実測トークン>
```

これにより `.specback/estimate-history.json` に `{timestamp, depth_mode, tone, num_chapters, num_units, estimated_tokens, actual_tokens}` が追記されます。実測が3件以上溜まると、以降の見積りは中央値の `実績 ÷ 見積り` 比で校正されます。

### 履歴の堅牢化

- `--record-actual` / `--budget-limit` は**正の整数のみ**受け付けます（0・負値は exit 2 で拒否）— 校正データが静かに汚染されるのを防ぎます。
- `estimate-history.json` への書き込みは**アトミック**（一時ファイル + リネーム）で、**symlink 経由の書き込みは拒否**します — 悪意あるリポジトリが `--record-actual` 経由で任意ファイルを上書きできません。
- 履歴に非有限な JSON 定数（`NaN` / `Infinity`）や壊れた JSON が含まれる場合は拒否し、破損ファイルは `estimate-history.json.bak` に隔離して新規から見積りを続行します。
- 履歴は直近50件に制限され（古い異常値の影響を薄める）、正の有限な `estimated_tokens` / `actual_tokens` を持つエントリのみが校正比に寄与します。

## 組み込みポイント

Phase 2 → Phase 3 の境界: `skills/specback/phases/phase-2-wbs.md` のステップ 6.5（"Token estimate & budget gate"）。見積りが予算を超える場合、スクリプトは `depth_mode` を `outline`（comprehensive の約半分のトークン）に切替えて再実行することを提案します。
