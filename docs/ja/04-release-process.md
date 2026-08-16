# リリース手順

## バージョニング

このプロジェクトは **Semantic Versioning**（`MAJOR.MINOR.PATCH`）に従います：

| 更新 | タイミング | 例 |
|------|-----------|----|
| **MAJOR** | 破壊的変更 | `v1.2.0` → `v2.0.0` |
| **MINOR** | 新機能、非破壊的拡張 | `v1.1.0` → `v1.2.0` |
| **PATCH** | バグ修正、ホットフィックス | `v1.2.0` → `v1.2.1` |

プレリリース接尾辞：`v1.2.0-alpha.1`、`v1.2.0-beta.1`

## 手順

### 1. CHANGELOGの準備

`CHANGELOG.md` が前回リリース以降の全変更を反映していることを確認します。形式は [Keep a Changelog](https://keepachangelog.com/ja/) に従います：

```markdown
## [v1.2.0] - 2026-08-01

### Added
- feat: specback-search MCP server (#270)
- feat: template catalog validation (#299)

### Changed
- chore: upgrade pytest to 9.x
```

### 2. READMEの更新

[`README.md`](../README.md) のバージョン参照（`## Status` セクションと「なぜ specback なのか？」のバージョン記述）を新バージョンに更新します。（旧「README Roadmap の更新」手順は廃止 — README に Roadmap セクションは存在しません）

### 3. タグ作成とプッシュ

```bash
# main上で、PRマージ後
git tag -a v1.2.0 -m "v1.2.0"
git push origin v1.2.0
```

### 4. GitHub Release の作成

```bash
gh release create v1.2.0 \
  --title "v1.2.0" \
  --notes "CHANGELOG.md を参照"
```

または手動で https://github.com/nekolife1984/specback/releases/new から作成します。

## ホットフィックスリリース

リリース済みバージョンへの緊急修正：

```bash
git checkout -b fix/hotfix-crash main
# 修正 → コミット → PR → squash merge → main
git tag -a v1.2.1 -m "v1.2.1 — crash fix"
git push origin v1.2.1
```

## リリース頻度

固定スケジュールはありません。以下のタイミングでリリースします：

- 意味のある機能マイルストーンに到達したとき
- 重要なバグが修正されたとき
- 破壊的変更の調整が必要なとき

現在の軌道：v1.2.0時点で安定版の v1.x シリーズ。破壊的変更がある場合は MAJOR バージョンの引き上げが必要です（上記バージョニング参照）。

## リリースチェックリスト

- [ ] CHANGELOG.md を更新した
- [ ] README のバージョン参照を更新した
- [ ] `skills/specback/SKILL.md` のバージョン文字列を更新した（該当時）
- [ ] タグを作成してプッシュした
- [ ] GitHub Release を作成した
