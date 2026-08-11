# specback-search MCP サーバー（specback-search-mcp）

> **ドキュメント**: [English](../en/10-specback-search-mcp.md) · [日本語](10-specback-search-mcp.md)

## 概要

生成済みの specback データ（`source-map.json` / `trace.json` / `inventory.json` / `questions.json` / `drift-report.json`）は、これまで `build-search-index.py` CLI 経由でしか照会できませんでした。Issue #270 は、その CLI をラップする **MCP（Model Context Protocol）stdio サーバー**を追加し、AI エージェント（Claude Code / Cursor / Hermes など）が CLI を叩かずに MCP ツールとしてネイティブに検索できるようにします。

サーバーは **stdlib only**（Python 3.10+、外部依存なし）です。MCP stdio トランスポート（stdin/stdout 上の改行区切り JSON-RPC 2.0）を直接実装しており、`mcp` SDK に依存しません。これにより導入コストがゼロになり、MCP エコシステムの仕様変動リスクを1ファイルに閉じ込めます。

## ツール

| ツール | 説明 | 主なパラメータ |
|--------|------|----------------|
| `specback_search` | ソースユニットを名前・パスで検索し、確度・章・ロールで絞り込み | `query`（任意 — `query`/`chapter`/`role`/`confidence` のいずれか1つ以上が必要）、`specback_dir`（デフォルト `.specback`）、`confidence`（🟢/🟡/🔴）、`chapter`、`role`、`format`（`text`/`json`） |
| `specback_uncovered` | いずれの章にもカバーされていないソースユニットを一覧 | `specback_dir`、`role`、`confidence`、`format` |
| `specback_drift` | 最新のドリフトレポートの要約を表示 | `specback_dir`、`format` |
| `specback_questions` | 未解決（または全）質問を表示 | `specback_dir`、`mode`（`open`/`all`、デフォルト `open`）、`format` |

全ツールとも `text`（CLI と同じ人間可読フォーマット）または `json`（機械可読）で出力します。各ツールが `specback_dir` を受け取るため、1つのサーバープロセスで複数プロジェクトを照会できます。1回の呼び出しで返すソースユニットは最大200件（text 出力は切り捨てを明記）、`query` は500文字・フィルタは200文字まで、50 MiB 超のアーティファクトは拒否します。

## サーバーの起動

```bash
python3 skills/specback-search/scripts/specback-search-mcp.py
```

サーバーは stdin から JSON-RPC メッセージを読み、stdout にレスポンスを書き、stdin が閉じるまで動作します（終了コード 0）。ログ・エラーは stderr のみに出力します — stdout はプロトコル専用です。

### トランスポートのスモークテスト

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
| python3 skills/specback-search/scripts/specback-search-mcp.py
```

期待値: `initialize` の result に `serverInfo.name = "specback-search"`、続いて `tools/list` の result に上記4ツールの定義。

## クライアント設定

### Claude Code

プロジェクトルート（またはユーザースコープ）の `.mcp.json`:

```json
{
  "mcpServers": {
    "specback-search": {
      "command": "python3",
      "args": ["/絶対パス/skills/specback-search/scripts/specback-search-mcp.py"]
    }
  }
}
```

### Cursor

`.cursor/mcp.json`（プロジェクト）またはグローバル MCP 設定 — 上記と同じ JSON 形式です。

### Hermes Agent

`~/.hermes/config.yaml` の `mcp_servers` に追加:

```yaml
mcp_servers:
  specback-search:
    command: python3
    args: ["/絶対パス/skills/specback-search/scripts/specback-search-mcp.py"]
```

Hermes を再起動すると、4ツールが `mcp_specback_search_*` として利用可能になります。

## プロトコル補足

- **トランスポート**: 1行1 JSON-RPC 2.0 オブジェクトの改行区切りで stdio 上を流れます（MCP stdio トランスポート）。Content-Length フレーミングはありません。
- **initialize**: サーバーはクライアントが要求した `protocolVersion` をエコーし（フォールバック `2024-11-05`）、`capabilities.tools.listChanged = false` を返します。
- **エラーコード**: `-32700` パースエラー、`-32601` メソッド未定義、`-32602` 不正パラメータ、`-32603` 内部エラー。
- **失敗時の挙動**: ツール呼び出しでサーバーが死ぬことはありません。`specback_dir` の欠如、`source-map.json` / `trace.json` の欠如（specback 未実行）、**破損（不正JSON）アーティファクト**、**FIFO/ソケット/デバイスアーティファクト**、サイズ超過アーティファクトはすべて `isError: true` のツール結果と実行可能なメッセージで返します。不正な stdin（壊れたJSON・不正UTF-8・深すぎるネスト・過大な行）にはプロトコルエラー（`-32700` / `-32600`）で応答し、サーバーは稼働を続けます。
- **Notification**（`id` を持たないメッセージ、例: `notifications/initialized` / `notifications/cancelled`）には応答しません。

## 信頼境界

本サーバーは、起動ユーザーと同じ権限で動作する**読み取り専用のローカルツール**です。クライアントが指定した `specback_dir` 内の任意のパスを読みます（`source-map.json` / `trace.json` / `inventory.json` / `questions.json` / `drift-report.json` と `final|drafts/*.md` 章ファイル。specback dir 外へ逃げる symlink は拒否）。**認証とパス封じ込めを追加しない限り、TCP や共有 MCP ゲートウェイに公開してはなりません**。stdin メッセージ行は 1 MiB・アーティファクトは 50 MiB・結果は1回200件までに制限し、メモリと LLM コンテキスト消費を抑えます。

## データの出所

`build-search-index.py` と同じです（[specback-search スキル](../../skills/specback-search/SKILL.md) 参照）:

- `source-map.json` / `trace.json` — 必須（先に specback Phase 0–3 を実行）
- `inventory.json` / `questions.json` / `drift-report.json` — 任意（`questions` は Phase 4 後、`drift` は `detect-drift.py` 実行後）

## テスト

```bash
uv run pytest skills/specback-search/scripts/tests/test_specback_search_mcp.py -v
```

プロトコル処理（initialize / tools/list / tools/call / ping）、4ツールの fixture データでの挙動、エラーセマンティクス、および subprocess でのエンドツーエンド stdio トランスポート（不正 JSON からの復旧・EOF での正常終了含む）を検証します。
