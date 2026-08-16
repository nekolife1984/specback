# テンプレートカタログ（templates/catalog.json）

> **ドキュメント**: [English](../en/11-template-catalog.md) · [日本語](11-template-catalog.md)

## 概要

9種類の仕様テンプレートは、各テンプレートのYAML frontmatter内に `detection_rules`（章カスタマイズ用の自動検出ルール、Issue #186 で展開済み）を持っています。Issue #299 以前は、テンプレートの**機械可読レジストリ**が存在せず、phase-1 recon のエージェントはテンプレートファイルを手作業で開いて適合性を判断する必要がありました。

`templates/catalog.json` がそのレジストリです。全テンプレートのメタデータを単一のJSONファイルに集約します：

| フィールド | 意味 |
|-----------|------|
| `name` | テンプレート名 — `templates/<name>.md` と frontmatter の `template_name` に対応 |
| `description` | 1行説明 — テンプレート frontmatter から同期 |
| `template_version` | テンプレートバージョン — テンプレート frontmatter から同期 |
| `chapters` | 章構成の順序付きリスト — テンプレートの `### Chapter N: Title` 見出しから同期 |
| `detection_rules` | `always_include` / `chapters` / `extra_chapters` / `optional` のIDリスト — テンプレートの `detection_rules` frontmatter ブロックから抽出 |
| `languages` | テンプレートが対象とする言語 — source_map_v2 抽出器のセット（`SUPPORTED_LANGUAGES`）から選択 |

カタログは**正（canonical）**です。テンプレートファイルを変更したら同じ変更でカタログも更新し、その逆も同様です。

## 検証

`scripts/validate-template-catalog.py` がカタログとテンプレートファイルの整合を維持します：

```bash
python3 scripts/validate-template-catalog.py
# ✅ catalog.json is consistent with 9 template(s).
```

検証内容：

1. **ファイル存在** — カタログの全エントリに対応するテンプレートファイルがあり、`templates/*.md`（`templates/ci/` を除く）の全ファイルにカタログエントリがある。
2. **frontmatter 同一性** — `template_name` / `description` / `template_version` が一致する。
3. **章構成** — `### Chapter N: Title` 見出しが `chapters` リストと一致する（順序も一致必須）。
4. **detection rules** — `always_include` / `chapters` / `extra_chapters` / `optional` のIDリストがテンプレートの `detection_rules` ブロックと一致する。
5. **REF形式** — deprecated な `[REF: ...]` 角括弧形式を拒否。テンプレートは HTMLコメント形式 `<!-- REF: ... -->` を使用する。
6. **言語** — `languages` の全エントリがサポート対象の抽出器言語である。

終了コード：`0` 整合、`1` 違反あり、`2` 使い方エラー。

## phase-1 recon での利用

`skills/specback/phases/phase-1-recon.md` のステップ2でカタログを読み、テンプレート候補を絞り込みます — 各エントリの `languages` をコードベースの検出言語と照合してから候補をユーザーに提示できます。ステップ3aではカタログエントリ（または検証ツールで同一に保たれるテンプレート frontmatter）から `detection_rules` を読みます。

## テンプレートの追加・変更手順

1. `templates/<name>.md` を編集する（frontmatter の `description` / `template_version` / `detection_rules`、または章見出し）。
2. `templates/catalog.json` の該当エントリを更新する（`description` / `template_version` / `chapters` / `detection_rules` / `languages`）。
3. `python3 scripts/validate-template-catalog.py` を実行し、コミット前に exit `0` を確認する。
