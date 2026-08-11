# インクリメンタル仕様更新

> **ドキュメント**: [English](08-incremental-update.md) · [日本語](08-incremental-update.md)

## 概要

ドリフト検出（Phase 7）は「どの仕様書セクションがソース変更の影響を受けたか」を教えてくれますが、章の更新は手作業でした。ドリフトレポートを読み、変更されたソースを読み直し、影響を受けた章を書き直し、`trace.json` を更新する——保守フェーズに入った仕様書ではこのループはほとんど回らず、仕様書は静かに腐敗していきました。

**インクリメンタル更新**（Issue #269）はそのループを閉じます。既存の `drift-report.json` を取り込み、**影響を受けた章だけ**を特定し、章単位の再調査プロンプト（Phase 3 のサブエージェント機構を章スコープで再利用）を生成します。そして更新された章を以下の 2 つの安全機構で検証・適用します:

1. **SRC-ID 再採番トラップガード** — 更新後の章に含まれる `<!-- REF: SRC-NNNN -->` がすべて `source-map.json` に存在しなければなりません。全再スキャンによる SRC-ID 再採番は REF を静かに壊しますが、このガードがそのような章の適用を拒否します。
2. **巻き込み変更ゼロ** — 対象の章だけが変更できます。`plan` 時点のベースラインと異なる他章ファイルがあれば検証失敗です。

## 仕組み

```text
detect-drift.py --json
        │
        ▼
drift-report.json
        │
        ▼
specback-incremental-update.py plan
        │   • impacted_sections から影響章を特定
        │   • 章単位の再調査プロンプトを生成
        │   • 章ハッシュのベースライン → state.json
        ▼
(agent) 各影響章を再調査
        │
        ▼
更新後の章ファイル
        │
        ▼
specback-incremental-update.py verify --updated <file>
        │   • 対象は影響章のいずれか？
        │   • REF の SRC-ID はすべて source-map.json に存在？
        │   • 他章はベースラインから未変更？
        ▼
specback-incremental-update.py apply --updated <file>
        • バックアップ → アトミック置換 → build-trace.py 再実行
```

## CLI リファレンス

```
python scripts/specback-incremental-update.py <plan|verify|apply> \
    --specback-dir .specback --output-dir specs [--drift-report drift-report.json]
```

| フラグ | デフォルト | 意味 |
|--------|-----------|------|
| `--specback-dir` | `.specback` | `wbs.json` / `source-map.json` / `trace.json` があるディレクトリ |
| `--output-dir` | `.` | 最終仕様書の章が置かれているディレクトリ（例: `specs/`） |
| `--drift-report` | `{specback-dir}/drift-report.json` | ドリフトレポート入力 |
| `--json` | off | plan/verify の機械可読 stdout |
| `--skip-trace-refresh` | off | `apply` — `build-trace.py` サブプロセスをスキップ |

### plan

`drift-report.json` を読み、`changes[].impacted_sections[].file` と `deleted_with_refs[].impacted_sections[].file` から影響を受けた章ファイル名を収集します。各影響章について:

1. `wbs.json` から章タイトルを取得。
2. その章のセクションでカバーされている SRC-ID を収集。
3. それらの SRC-ID にマップする変更済みソースファイルを収集。
4. **章単位の再調査プロンプト**を `{specback-dir}/incremental/prompts/{chapter_file}.md` に生成。
5. 章の sha256 を `{specback-dir}/incremental/state.json` にスナップショット（巻き込み変更ゼロのベースライン）。

終了コード: `0` 成功 · `1` 入力欠落/読取不能 · `2` 影響章なし。

### verify

`--updated PATH` — 再調査された章ファイル。以下のチェック:

- **対象チェック**: ベース名が `plan` で記録された影響章のいずれかであること（`state.json` が無い場合は exit `3` — 先に plan を実行）。
- **SRC-ID 存在**: `<!-- REF: SRC-NNNN -->` がすべて `source-map.json`（`units[].id`）に存在すること。欠落は再採番トラップ違反。`<!-- REF: path:line -->` 形式は対象外（Phase 7b のツールが担当）。
- **巻き込み変更ゼロ**: `{output_dir}` 配下の他章ファイルがすべてベースラインハッシュと一致すること。対象章自体は変化している必要あり（変化が無ければ警告）。

終了コード: `0` 合格 · `1` 失敗 · `3` state.json 欠落。

### apply

`verify` と同じチェックを実行してから:

1. 現在の章を `{output_dir}/{target}.pre-incremental` にバックアップ。
2. アトミックに置換（一時ファイル + `os.replace`）。
3. `build-trace.py` で `trace.json` を更新（`--skip-trace-refresh` でスキップ可）。

終了コード: `0` 成功 · `1` チェック失敗 or build-trace 失敗 · `3` state.json 欠落。

## 再調査プロンプト

`plan` は影響章ごとに自己完結型のプロンプトを生成します。内容: ドリフトの文脈（generated_at、base）、その章に影響する変更済みソースファイル、再チェックすべき SRC-ID、REF を有効に保ち章の外に触れないための指示。章スコープに制限された Phase 3 サブエージェント機構であり、再調査コストはドリフトの規模に比例します。

## 安全設計（Issue #269）

| ガード | 仕組み |
|--------|--------|
| SRC-ID 再採番トラップ | `verify` が `source-map.json` に存在しない `<!-- REF: SRC-NNNN -->` を含む章を拒否 |
| 巻き込み変更ゼロ | `plan` が全章ハッシュをスナップショット、`verify` が対象外の変更で失敗 |
| アトミック適用 | バックアップ + 一時ファイル + `os.replace`。唯一のコピーをその場で切り詰めない |
| 冪等な状態 | `state.json` は `plan` が再生成。状態欠落時は `verify`/`apply` を拒否 |
| 入力安全性 | JSON 入力は 50 MiB 上限、非 dict JSON は exit 1 で拒否 |

## 利用例

```bash
# 1. ドリフト検出（Phase 7）
python scripts/detect-drift.py --specback-dir .specback --output-dir specs --json

# 2. インクリメンタル更新の計画
python scripts/specback-incremental-update.py plan \
    --specback-dir .specback --output-dir specs --json

# 3. (agent) 各影響章を再調査
#    .specback/incremental/prompts/<chapter>.md を使って
#    .specback/incremental/updated/<chapter>.md に保存

# 4. 更新の検証（SRC-ID ガード + 巻き込みゼロ）
python scripts/specback-incremental-update.py verify \
    --specback-dir .specback --output-dir specs \
    --updated .specback/incremental/updated/05-data-model.md

# 5. 適用 + trace 更新
python scripts/specback-incremental-update.py apply \
    --specback-dir .specback --output-dir specs \
    --updated .specback/incremental/updated/05-data-model.md
```

## 関連

- Phase 7 ドリフト検出: `skills/specback/phases/phase-7-drift.md`
- ドリフト CI 自動化: [05-drift-ci.md](05-drift-ci.md)
- SRC-ID 安全機構の先例: `restore-sourcemap-from-trace.py`（idempotency ガード）
