# ブランチ戦略 — GitHub Flow

## 概要

このプロジェクトは **GitHub Flow** を採用しています。単一の永続ブランチ（`main`）と短命なフィーチャーブランチによる軽量なワークフローです。

> GitHub Flow を選んだ理由：
> - 単独開発者 — 最小限のルールで運用可能
> - 既存の規約（PR必須、squash merge、1変更1commit）と完全に合致

## 永続ブランチ

| ブランチ | 保護 | 用途 |
|---------|------|------|
| `main` | ✅ 直push禁止 | 単一の真実。すべての変更はPR経由。 |

## ブランチ命名規則

すべての作業ブランチは `main` から派生し、マージ後に削除します。

| プレフィックス | 例 | 用途 |
|--------------|-----|------|
| `feat/<kebab-case>` | `feat/add-plantuml-template` | 新機能・機能拡張 |
| `fix/<kebab-case>` | `fix/detect-py-encoding` | バグ修正 |
| `chore/<kebab-case>` | `chore/update-deps` | CI・メンテナンス・リファクタリング・依存関係 |
| `docs/<kebab-case>` | `docs/branching-strategy` | ドキュメントのみ |

## PRライフサイクル

```
main → feat/xxx → コミット → PR作成 → CI (pytest + pyrefly + trace gates)
                                    ↓
                          全部通った？ → squash merge → ブランチ削除
                                    ↓
                          失敗？ → 修正&push → CI再実行
```

### ルール

1. **最新の `main` からブランチを作成** — 遅れていたら rebase
2. **短命ブランチ** — 数時間〜数日、数週間は不可
3. **1ブランチ = 1論理変更** — 1つのconventional commitに対応
4. **Squash merge** — `main` の履歴を一直線に保つ
5. **マージ時のコミットメッセージ** — `feat: description (#N)`
6. **マージ後はブランチを削除** — リモート・ローカルともに

### 直push例外

| 変更種別 | 直push？ | 条件 |
|---------|---------|------|
| タイポ・コメント修正 | ✅ 許可 | CI通過 |
| CI設定の微調整 | ✅ 許可 | 動作確認済み |
| 軽微なドキュメント | ✅ 許可 | 仕様や内容の変更なし |
| ソースコード・テスト・機能 | ❌ **PR必須** | CI + trace gates通過 |

単独開発者でも **すべてPR経由が推奨** — 自分自身の変更を再確認する習慣になる。

## CIゲート

GitHub Actions（`.github/workflows/ci.yml`）がすべてのPRで自動実行されます。詳細は [03-pr-review-process.md](03-pr-review-process.md) を参照してください。

## リリース手順

完全なリリース手順（バージョニング、CHANGELOG）は [04-release-process.md](04-release-process.md) を参照してください。

簡単なタグ作成コマンド：

```bash
git tag -a v0.8.0 -m "v0.8.0 — description"
git push origin v0.8.0
```

## 関連ドキュメント

| ドキュメント | 説明 |
|-------------|------|
| [02-commit-conventions.md](02-commit-conventions.md) | Conventional Commits形式、1変更1コミットルール |
| [03-pr-review-process.md](03-pr-review-process.md) | PRライフサイクル、テンプレート、レビュアーチェックリスト、squash merge |
| [04-release-process.md](04-release-process.md) | バージョニング、CHANGELOG、リリースチェックリスト |
