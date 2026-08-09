# Contributing to specback

フィードバック・テンプレート追加要望・バグ報告は [GitHub Issues](https://github.com/nekolife1984/specback/issues) にて受け付けます。

特に以下の貢献を歓迎します：

- 新しい言語・フレームワークのインベントリ単位定義
- 新しいテンプレート（DWH、機械学習パイプライン、IaC、モバイルアプリ 等）
- 検証チェックリストの拡充
- 実プロジェクト適用例のレポート

## クイックスタート

```bash
# 0. テストに必要な依存をインストール（tree-sitter grammars は任意）
pip install -r scripts/dev-requirements.txt

# 1. main からブランチを作成
git checkout main
git pull origin main
git checkout -b feat/your-feature

# 2. 変更を加えてコミット
git add .
git commit -m "feat: add plantuml template"

# 3. PRを作成
git push origin feat/your-feature
# → GitHubでPRを作成
```

## 開発ガイド

| ガイド | 説明 |
|--------|------|
| [ブランチ戦略](docs/ja/01-branching-strategy.md) | GitHub Flow、ブランチ命名規則、上流同期 |
| [コミット規約](docs/ja/02-commit-conventions.md) | Conventional Commits、1変更1コミット、メッセージ形式 |
| [PRレビュープロセス](docs/ja/03-pr-review-process.md) | PRテンプレート、レビュアーチェックリスト、squash merge |
| [リリース手順](docs/ja/04-release-process.md) | バージョニング、CHANGELOG |

## ルール

- **1変更1コミット** — コミットメッセージは `feat:` / `fix:` / `chore:` / `docs:` / `upstream:` のプレフィックスを使用
- **PR必須** — ソースコード・テスト・機能変更は必ずPR経由
- **Squash merge** — mainの履歴をまっすぐ保つ
- **CIゲート通過必須** — GitHub Actions（`.github/workflows/ci.yml`）が PR 上で自動実行:
  - `pytest`（scripts/ および source_map_v2/）
  - `mypy`（アドバイザリ、警告表示）
  - Smoke import チェック（全スクリプトの import 検証）

> **💡 テストスキップについて:** tree-sitter grammars 未インストール時は一部テストがスキップされます（`pytest -rs` で理由確認可）。CI では `requirements.txt` 経由で自動インストールされるため全件実行されます。
- **ドキュメント同期** — EN + JA の両方を更新（ドキュメント変更時）

## 言語・フレームワークを追加する

`scripts/source_map_v2/` に新しい言語やフレームワークの抽出ロジックを追加する手順です。
既存の `python_ext.py` や `go_ext.py` をテンプレートとして参照してください。

### 編集するファイル

| # | ファイル | 変更内容 |
|---|---------|---------|
| 1 | `scripts/source_map_v2/detect.py` | `LANG_BY_EXT` に拡張子マッピングを追加。必要に応じて `detect_frameworks()` にフレームワーク判定ロジックを追加 |
| 2 | `scripts/source_map_v2/extractors/<lang>_ext.py` | **新規作成**。`Extractor` サブクラスを実装し `@register` デコレータで登録 |
| 3 | `scripts/source_map_v2/extractors/__init__.py` | `_autoload()` のモジュールリストに対象モジュールを追加 |
| 4 | `scripts/source_map_v2/taxonomy.py` | 新しい kind が必要な場合 `register_kind()` の呼び出しを追加（既存の role で足りる場合は不要） |
| 5 | `scripts/source_map_v2/extractors/tshelpers.py` | tree-sitter を使用する場合のみ。言語固有のクエリ追加（任意） |

#### 各ファイルの詳細

**1. detect.py — 拡張子とフレームワーク判定**

```python
# LANG_BY_EXT に拡張子を追加
LANG_BY_EXT = {
    # ... 既存のエントリ ...
    ".rs": "rust",   # 追加
}

# detect_frameworks() にフレームワーク判定を追加（該当する場合）
def detect_frameworks(root: Path) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    # ... 既存の判定 ...
    if (root / "Cargo.toml").exists():
        hints.append(_hint("rust", "cargo", "medium", "Cargo.toml present"))
    return hints
```

**2. エクストラクタ — 新規ファイル作成**

`scripts/source_map_v2/extractors/<lang>_ext.py` を作成：

```python
\"\"\"Extractor for your language.\"\"\"

from __future__ import annotations

from typing import Callable

from .. import taxonomy
from ..model import SourceUnit, fingerprint
from . import register, Extractor


class YourLangExtractor(Extractor):
    language = "your_lang"

    def extract(
        self,
        path: str,
        source: str,
        id_factory: Callable[[], str],
        framework: str | None = None,
        context: dict | None = None,
    ) -> list[SourceUnit]:
        units: list[SourceUnit] = []
        # 実装例:
        # - ソースをパースして units に SourceUnit を追加
        # - role-typing は taxonomy.role_for_kind(kind) で解決
        return units


register(YourLangExtractor())
```

**3. __init__.py — 自動ロードに追加**

```python
_autoload() のモジュールリストに追加:
for mod in ("python_ext", "typescript_ext", ..., "your_lang_ext"):
```

### 必須テスト

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `scripts/source_map_v2/tests/test_<lang>_ext.py` | **新規作成**。最低1つの fixture コードと期待値 |
| 2 | 既存テスト群 | 回帰テスト — `pytest scripts/source_map_v2/tests/` が通ることを確認 |

テストファイルのテンプレート：

```python
\"\"\"Tests for your_lang extractor.\"\"\"

from source_map_v2 import build_source_map
from source_map_v2.model import SourceUnit


# 言語の fixture コード（インライン文字列、または Path 参照）
FIXTURE_CODE = '''
// サンプルコード
fn hello() -> String {
    "Hello, world!".to_string()
}
'''


def test_your_lang_units() -> None:
    units = _run_extractor(FIXTURE_CODE)
    assert len(units) > 0


def test_your_lang_role_typing() -> None:
    units = _run_extractor(FIXTURE_CODE)
    endpoint_units = [u for u in units if u.role == "endpoint"]
    # role-typing の期待値をアサート
```

### ドキュメント更新

| # | ファイル | 変更内容 |
|---|---------|---------|
| 1 | `references/inventory-units.md` | 言語セクションを追加。対応フレームワークの典型的な抽出単位を記載 |
| 2 | `README.md` | 「Supported Languages」セクションに対象言語を追記 |
| 3 | `README.md` | Roadmap から該当マイルストーンを ~~完了~~ に |

### CI 確認

マージ前に以下をすべて確認：

```bash
# 全テスト
pytest scripts/source_map_v2/tests/ -v

# Smoke import（source_map_v2 が正しくロードされるか）
python -m source_map_v2 --target tests/fixtures/your_lang/

# mypy（非ブロッキング、新規警告がないことを確認）
mypy scripts/source_map_v2/ --ignore-missing-imports --follow-imports=skip
```

## テンプレートを追加する

`templates/` に新しい仕様書テンプレートを追加する手順です。

### 編集するファイル

| # | ファイル | 変更内容 |
|---|---------|---------|
| 1 | `templates/<name>.md` | **新規作成**。既存テンプレート（例: `web-app.md`）を参考に構造化 |
| 2 | `references/template-catalog.md` | 新しいテンプレートのエントリを追加 |
| 3 | `README.md` | 「Templates」セクションにテンプレート名を追記 |
| 4 | `README.md` | Roadmap から該当マイルストーンを ~~完了~~ に（該当する場合） |

### テンプレート構造の要件

- `## Sources Read` セクションを含める
- `<!-- REF: ... -->` マーカーでトレーサビリティを確保する（SRC-ID形式 `<!-- REF: SRC-NNNN -->` を推奨）
- `<!-- CONFIDENCE: ... -->` ラベルで推測と確定を区別する
- Mermaid ダイアグラムを含める（該当する場合）

## 検証チェックリストを拡充する

`references/verification-checklists.md` に新しいチェック項目を追加する手順です。

### 編集するファイル

| # | ファイル | 変更内容 |
|---|---------|---------|
| 1 | `references/verification-checklists.md` | 新しいチェック項目を適切なカテゴリに追加 |
| 2 | `README.md` | Roadmap から該当マイルストーンを ~~完了~~ に（該当する場合） |

### 追加時の注意点

- 各チェック項目は「確認内容」「合格条件」「確認方法」を明確に記載
- 自動チェックと手動チェックを区別
- 既存の `coverage-check.py` で検証可能な項目はスクリプト側も更新を検討

## 英語版

For English: see the [English README](README.md) and [docs/en/](docs/en/) directory.
