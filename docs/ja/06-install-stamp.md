# スキルスタンプ — インストール体験

## 概要

**スキルスタンプ** 方式のインストールは、SSSFに着想を得たスタンプ分離をspecbackに導入します。
従来のように全ファイルをスキルディレクトリにコピーするのではなく、対象プロジェクトに
3層のクリーンな構造を作成します：

```
target-repo/
├── .specback_data/              ← プロジェクト固有（キミがカスタマイズ）
│   ├── templates/                   → カスタムテンプレート（初期は空）
│   ├── prompt_engineering/          → カスタムプロンプト（初期は空）
│   └── llockfile                    → インストールハッシュのロックファイル
│
└── .claude/skills/specback/     ← コアスキル（読み取り専用スタンプ）
    ├── SKILL.md
    ├── phases/
    ├── scripts/
    ├── references/
    └── templates/
```

`llockfile`（JSONロックファイル）は、スタンプ時に全ファイルのSHA-256ハッシュを記録します。
これによりドリフト検出が可能になり、スタンプされたファイルが変更・追加・削除された場合に
警告を出せます。

## 使い方

### 基本スタンプ

```bash
# specbackリポジトリルートから：
python3 scripts/specback_install.py /path/to/target-repo

# または install.sh ラッパー経由：
./install.sh /path/to/target-repo
```

### ドリフト検出

```bash
python3 scripts/specback_install.py --check /path/to/target-repo

# 出力例：
#   🔍  Drift check for: /path/to/target-repo
#      Installed: 2026-08-05T12:00:00Z
#      Version:   1.2.0
#   🔴  Modified files (1):
#        ~ .claude/skills/specback/SKILL.md
#   ✅  Intact files: 262
```

### 強制再スタンプ

```bash
python3 scripts/specback_install.py --force /path/to/target-repo
```

> **注意:** `--force` 実行前は `git commit` を推奨します。ローカルのカスタマイズを
> 失わないようにするためです。

### ドライラン

```bash
python3 scripts/specback_install.py --dry-run /path/to/target-repo
```

実際にファイルを書き込まずに、スタンプされる内容を表示します。

### ヘルプ

```bash
python3 scripts/specback_install.py --help
```

## ロックファイル

ロックファイルは `.specback_data/llockfile` に配置されるJSONファイルです：

```json
{
  "installed_at": "2026-08-05T12:00:00Z",
  "specback_version": "1.2.0",
  "hashes": {
    ".claude/skills/specback/SKILL.md": "sha256:ghi789..."
  },
  "user_modified": []
}
```

`hashes` セクションには、全スタンプファイルのSHA-256ダイジェストが
ターゲットルートからの相対パスをキーとして格納されます。`--check` コマンドは
これらを現在のファイルハッシュと比較します。

## スタンプ内容

| レイヤー | ソース（specbackリポジトリ） | ターゲット | 再スタンプ可能？ |
|---------|---------------------------|----------|:------------:|
| コアスキル | `skills/specback/` | `target/.claude/skills/specback/` | はい |
| 検索スキル | `skills/specback-search/` | `target/.claude/skills/specback-search/` | はい |
| 共有アセット | `scripts/`, `references/`, `schemas/`, `agents/`, `templates/`, `variants/` | `target/.claude/skills/specback/...` | はい |
| カスタムテンプレート | （空） | `target/.specback_data/templates/` | いいえ（ユーザーデータ） |
| カスタムプロンプト | （空） | `target/.specback_data/prompt_engineering/` | いいえ（ユーザーデータ） |

`.specback_data/` 以下のファイルは再スタンプ時に**決して上書きされません**。
これらはプロジェクト固有のカスタマイズ領域です。

## レガシーインストールからの移行

`install.sh` ラッパーはスタンプモードを自動検出します：

- **パス引数**（例：`/path/to/target`）が渡された場合 → `scripts/specback_install.py` に委譲
- レガシーの **`--agent`** または **`--level`** フラグが渡された場合 → 従来の
  エージェントインストールフローを実行（Claude Code、OpenCode、Copilot等に対応）

両モードは共存できます：`./install.sh /repo` でプロジェクトにスタンプし、
`./install.sh --agent claude --level user` で同時にエージェントにスキルを
インストールできます。

## 受け入れ基準

- [x] `specback install /path` が正しくファイルをスタンプ
- [x] `.specback_data/` がプロジェクトデータとスタンプファイルを分離
- [x] `--check` がドリフト（変更/追加/削除）を検出
- [x] ロックファイルが作成され `--check` で検証される
- [x] `--force` がスタンプファイルを上書き
- [x] `--dry-run` がスタンプ内容を表示
- [x] `install.sh` ラッパーがスタンプ/レガシー両方で動作
- [x] 既存の `install.sh` 動作が維持されている（後方互換）
- [x] テストが通過
