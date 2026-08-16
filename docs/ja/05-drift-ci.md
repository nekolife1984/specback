# ドリフト検出のCI自動化

> **ドキュメント**: [English](../en/05-drift-ci.md) · [日本語](05-drift-ci.md)

## 概要

specback が生成した仕様書は、生成直後からコードとの乖離（ドリフト）が始まります。ドリフト検出の仕組み（`detect-drift.py` / `gates.py` / `git_utils.py`）は従来 Phase 7 の**手動実行**のみだったため、実際には誰も定期的に実行せず、「正直な仕様書」は数週間で「美しいフィクション」に逆戻りしていました。

本ドキュメントは、既存のドリフト資産を毎日の開発フローに接続する**CI 自動化**（Issue #266）について説明します:

- PR ごとに走る GitHub Action（`specback-drift`）
- ローカルでも同一動作する薄いラッパー（`scripts/specback-gate.py`、`--ci` モード = `act` 不要）
- opt-in の pre-push フック（`scripts/install-drift-hooks.sh`、初期値は warn モード）

## 仕組み

ラッパーは既存の検証スクリプトを連結します:

```text
git merge-base(origin/<base>, HEAD)
        │
        ▼
detect-drift.py --base <merge-base> --json
        │                       │
        │                 drift-report.md / drift-report.json
        ▼
fix-refs.py --check            (孤立REF検出 — 警告のみ)
        │
        ▼
drift-report 健全性             (gates.py --gate drift_detected と同等)
        │
        ▼
verdict: pass / warn / fail
```

### warn/fail 2段階ポリシー

| Verdict | Exit | 意味 |
|---------|------|------|
| **fail** | 1 | `drift-report.json` が影響セクション or 削除済みソース参照を報告。この PR 内で仕様書を更新する必要あり。 |
| **warn** | 0 | fail レベルのドリフトは無いが、孤立 REF や新規未カバーソースを検出。ゲートはしない。 |
| **pass** | 0 | ドリフトなし、警告なし。 |

### base 解決（merge-base）

CI では diff の base に **merge-base**（PR ブランチと base ブランチの分岐点）を使います。`state.json` の `generated_at_commit`（生成時点のスナップショット）を使うと、生成後の全コミットがドリフトとして報告されてしまうためです。

解決優先順位:

1. 明示的な `--base` CLI 引数
2. CI モード（`--ci`）: `GITHUB_BASE_REF` → `origin/<base>` の merge-base、次に `origin/main` の merge-base
3. ローカルデフォルト: `origin/main` の merge-base
4. `HEAD`（最終手段）

すべての ref は `git_utils.resolve_ref()`（SAFE_REF_RE + rev-parse）を通るため、`--base` による git オプション注入は拒否されます（Issue #253 の堅牢化）。

### spec 未生成時のスキップ

specback をまだ実行していないプロジェクトには `source-map.json` / `trace.json` がありません。この場合、ゲートは **warn**（exit 0）を返し「spec artifacts missing — run specback to generate the spec first」と報告します。仕様書が生成されて初めてドリフトゲートが適用されるため、新規リポジトリでワークフローを有効化しても CI は壊れません。

### fix-refs 失敗時のハンドリング

`fix-refs.py --check --json` ステップは警告のみですが、その出力契約は強制されます: fix-refs が非ゼロで終了した場合、または stdout が期待される JSON でない場合（将来の契約変更など）、ゲートは「0 orphaned / ok」と黙って報告**しません**。代わりに warning を記録し、JSON 出力で `fix_refs.ok = false` を設定します。壊れた統合が誤合格ではなく可視化されるためです（Issue #313）。

## GitHub Action

### 対象プロジェクトでの利用

`templates/ci/specback-drift.yml.example` を `.github/workflows/specback-drift.yml` にコピーして調整します:

```yaml
- uses: nekolife1984/specback/.github/actions/specback-drift@main
  with:
    specback-dir: .specback      # .specback/ へのパス
    fail-on-drift: "false"       # "true" でドリフト時にビルド失敗
    comment-on-pr: "true"        # PR にサマリーコメントを投稿
```

要件:

- checkout ステップに **`fetch-depth: 0`** が必要（merge-base 解決にフル履歴が必要）
- ジョブに `permissions.pull-requests: write` が必要（PR コメント投稿のため）
- Action は `GITHUB_BASE_REF` から `origin/<base>` を解決します（PR の checkout では base ブランチの merge-base コミットは取得済み）

Action は `specback-gate.py --ci` を実行し、次のようなコメントを投稿します:

```markdown
## 🤖 specback drift check
**Status: 🔴 FAIL — the spec is affected by this PR.**

- **Changed files**: 3
- **Affected spec sections**: 2
- **New uncovered sources**: 1
- **Deleted sources with refs**: 0
```

### PR コメント生成

コメント本文は `.github/actions/specback-drift/post-comment.py` が生成します。報告すべき内容が無い場合（変更なし・pass）はコメントをスキップします。コメントは提供された token で `gh pr comment` により投稿されます。

**フォークPRの場合**: フォークからのPRではコメント投稿をスキップします。フォークPRのデフォルト `GITHUB_TOKEN` は `pull-requests: read` のみのため、`gh pr comment` は `Resource not accessible by integration (addComment)` で失敗します。アクションはフォーク判定（`head.repo.full_name != github.repository`）を行い、ジョブを失敗させる代わりにコメントステップをスキップします（#339）。

## ローカル検証モード（`--ci`）

`act` を使わずに同一動作をローカルで再現できます:

```bash
# CI モード: GITHUB_BASE_REF または origin/main から merge-base を解決
python scripts/specback-gate.py --ci --specback-dir .specback --json

# 明示的な base
python scripts/specback-gate.py --base origin/main --specback-dir .specback --json

# pre-push スタイル: ブロックせず warn のみ
python scripts/specback-gate.py --ci --warn-only --specback-dir .specback
```

終了コード: `0` = pass/warn、`1` = fail（ドリフト）、`2` = 使い方/環境エラー。

## opt-in pre-push フック

GitHub Actions を使わない（または併用したい）プロジェクト向けに、opt-in の pre-push フックを提供します。**初期値は warn モード** — ドリフトを検出しても push はブロックしません。

```bash
# 対象プロジェクトのルートで実行
sh /path/to/specback/scripts/install-drift-hooks.sh

# fail モード（ドリフト時に push をブロック）
SPECBACK_FAIL_ON_DRIFT=1 sh /path/to/specback/scripts/install-drift-hooks.sh
```

動作:

- 既存の pre-push フック（`pre-push.specback-backup` に退避）を連鎖実行する `.git/hooks/pre-push` を生成
- push のたびに `specback-gate.py --ci`（warn モード）を実行
- アンインストール: `rm .git/hooks/pre-push && mv .git/hooks/pre-push.specback-backup .git/hooks/pre-push`

## 他の CI システム

GitLab CI のサンプルを `templates/ci/specback-drift.gitlab-ci.yml.example` に用意しています。マージリクエストのパイプラインで同じ `specback-gate.py --ci` を実行し、`drift-report.md/json` をアーティファクトとして保持します。

## ドッグフーディング

specback 自身のリポジトリでもこのワークフローを有効化しています（`.github/workflows/specback-drift.yml`、`fail-on-drift: "false"`）。specback リポジトリは現時点で完全な `.specback/` 自己生成を持たないため、PR では **warn**（"spec artifacts missing"）が報告されます — これは Action の配線が正常であることのライブチェックも兼ねています。

## ファイル一覧

| ファイル | 役割 |
|---------|------|
| `scripts/specback-gate.py` | 薄い CI ラッパー（merge-base → detect-drift → fix-refs --check → 健全性） |
| `scripts/install-drift-hooks.sh` | opt-in pre-push フックのインストーラー（初期値 warn モード） |
| `.github/actions/specback-drift/action.yml` | composite GitHub Action |
| `.github/actions/specback-drift/post-comment.py` | PR コメント本文の生成 |
| `.github/workflows/specback-drift.yml` | specback 自身のドッグフーディング用ワークフロー |
| `templates/ci/specback-drift.yml.example` | 対象プロジェクト向け GitHub Actions テンプレート |
| `templates/ci/specback-drift.gitlab-ci.yml.example` | GitLab CI サンプル |
