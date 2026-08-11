# Changelog

All notable changes to the specback project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [v1.2.0] - 2026-08-01

### Added

- `specback-search` スキルを追加。`build-search-index.py` CLI で生成済み JSON データ（source-map, trace, inventory, questions, drift）を名前・未カバー・章・ロール・確度・ドリフトの観点で検索可能に。Python 3.10+ stdlib only ([#152], [#153])
- `install.sh` / `install.ps1` を更新。specback-search スキルも specback 本体と同時にインストールされるように ([#153])

### Fixed

- `skills/specback-search/SKILL.md` を日本語から英語に統一。プロジェクトの方針（スクリプト・CLIは英語）に準拠 ([#154])

## [Unreleased]

### Added

- ドリフト検出の CI 自動化 ([#266]): `scripts/specback-gate.py`（薄い CI ラッパー: merge-base 解決 → detect-drift → fix-refs --check → レポート健全性）、`specback-drift` GitHub Action（composite action + PR コメント投稿）、opt-in pre-push フック `scripts/install-drift-hooks.sh`（初期値 warn モード）、GitHub Actions / GitLab CI テンプレート（`templates/ci/`）、ローカル検証モード（`--ci`、`act` 不要）、specback 自身でのドッグフーディング導入
- トークン見積り & バジェットゲート ([#267]): `scripts/specback-estimate.py`（Phase 2 完了時点の `inventory.json` / `goal.json` / `wbs.json` から Phase 3 の推定トークン消費量を出力。モデル非依存・料金は出力しない。`--json` / `--budget-limit` / `--record-actual` に対応し、実測3件以上で中央値比により校正）。Phase 2 → Phase 3 境界（`phase-2-wbs.md` ステップ 6.5）に組み込み

### Fixed

- `specback-estimate.py` の堅牢化 (agency review 事後対応): `--record-actual` / `--budget-limit` の正値検証（0・負値拒否）、`estimate-history.json` のアトミック書き込み + symlink 拒否（任意ファイル上書き防止）、非有限 JSON 定数（NaN/Infinity）と破損履歴の拒否・`.bak` 隔離、履歴の最大50件キャップと不正エントリ除外、`depth_mode` / `tone` の型ガードと制御文字サニタイズ、入力ファイルのサイズ上限（50 MiB）。テストを 20 → 35 件に拡充

## [v1.1.0] - 2026-08-01

### Added

- `depth_mode` 自動選択の閾値をファイル数→コード行数ベースに変更。小規模コードベースは自動で outline モードに ([#146])
- `goal.json` に `tone` フィールドを追加（`concise` / `thorough`）。Phase 1 で depth_mode と同時に選択、Phase 3 以降の品質基準を tone で制御 ([#146])
- `pyproject.toml` を追加。パッケージメタデータと mypy 設定を一元管理 ([#137])
- mypy を CI ブロッキングチェックに昇格。全9件の型警告を修正し、`dev-requirements.txt` でローカルでも利用可能に ([#138])
- pre-commit hook に EN/JA ドキュメント同期チェックを追加（WARNING表示、ブロックはしない）([#140])
- `coverage-check.py` の `--min-lines-per-chapter` デフォルトを 0 に変更（tone-guided）。固定の200行品質基準を撤廃 ([#146])

### Changed

- フェーズファイル（`phase-*.md`）を `phases/` サブディレクトリに移動。補助ドキュメント（`question-bank.md` 他）を `docs/` に移動 ([#148])
- depth_mode 選択の閾値を「ファイル数 ≤ 200 → comprehensive」から「コード行数 ≤ 500 → outline」に変更 ([#146])
- 「1章200行以上」の固定品質基準を撤廃。代わりに tone ベースのガイダンスに置き換え ([#146])

### Fixed

- 3つのテストが tree-sitter grammar 未インストールのローカル環境で FAIL する問題を修正。grammar 不足時はスキップ、monkeypatch も修正 ([#135])
- `.opencode/skills/specback/` の重複を削除（88ファイル削除）。`skills/` を単一ソースに統一 ([#136])
- 生成ドキュメントに specback 内部ファイルパスが漏れる問題を修正 ([#134])
- フォルダ整理後の未更新参照パスを修正（SKILL.md、state-management.md 内の12箇所）([#149])
- `merge-pr.sh` の CI ステータス取得処理を修正（pending/fail を取得失敗と誤判定）([#124])
- tree-sitter core バージョンを 0.25.1 に固定し、`--install-deps` のドキュメントを明確化 ([#125])
- 警告文の正確性を改善（import バグと grammar 欠落の区別）([#120])
- `coverage-check.py` のコードブロック行を body-lines ゲートにカウントするよう修正 ([#118])
- `coverage-check.py` の `--output-dir` と `--target-dir-for-required` の fallback 解決を追加 ([#117])

### Docs

- `CONTRIBUTING.md` にテスト依存のインストール手順と `pytest -rs` によるスキップ理由確認方法を追記 ([#139])
- テンプレートの章順設計原則（読者の理解順）を明文化 ([#129])
- Phase 2 WBS のハードコードされた章数記述をテンプレート非依存の動的記述に修正 ([#126])
- 自己文書化 `specs/` をテンプレートの章構成更新に追従させ再生成 ([#127])

## [v1.0.0] - 2026-07-30

### Added

- Kotlin extractor for source_map_v2 ([#37])
- C, C++, Dart, Swift extractors for source_map_v2 ([#42])
- Rust extractor for source_map_v2 ([#52])
- Knowledge Graph (JSON-LD) export from source-map.json and trace.json ([#53])
- GitHub Actions CI workflow with pytest, mypy, and smoke import checks ([#32])
- `--agent` / `--level` CLI flags for install.sh and install.ps1 ([#35])
- Phase 7c ChangeSpec — change specification generation option ([#14])
- Phase 7 Drift Detection + Phase 7b REF Auto-Fix with hash mode ([#12])
- Resume phase → file loading instructions
- SKILL.md split into per-phase files with lightweight index ([#18])
- Multi-agent skill installer (install.sh / install.ps1)
- Pre-commit hook enforcing tests for new scripts ([#13])
- Phase 0: skill path recording via `.specback/.skill-path` (replaces bundle staging copy)
- Customizable output directory for specification documents ([#10])
- Pre-push hook blocking direct pushes to main ([#1])
- GitHub Flow branching strategy docs (EN + JA)
- Japanese-localized PR/Issue templates ([#2])
- Python dependency management via requirements.txt with `--install-deps` installer option ([#55])
- モノレポ構成でシステム単位にスコープを分割して仕様書生成に対応 ([#106])
- システム設計書（システム設計・横断的設計・モジュール依存関係）の生成に対応 ([#103])
- 機能仕様書（機能一覧と各機能の処理定義）の生成に対応 ([#90])
- 複雑な処理を積極的にMermaid図で図示する Active-diagram ルールを追加 ([#95])
- JSON Schema を同梱し validate-schema.py による機械検証を実装 ([#82])
- coverage-check.py が goal.json の template フィールドを参照してデフォルト閾値を自動調整 ([#83])

### Changed

- Repository renamed from `cc-rsg` to `specback` ([#57])
- source_map_v2 role-typing connected to Phase 2 inventory.json ([#36])
- Drafts always go to `.cc-rsg/drafts/` regardless of output_dir
- Final spec output path simplified (no `/final/` subdirectory)
- Kept `.cc-rsg/final/` as default path; custom paths go direct
- Removed Claude Code-specific wording from docs and skill
- Removed Versioning/changelog and License sections from SKILL.md (moved to separate files)
- Updated PR template references (CHANGELOG.md / daishir0) ([#51])
- Library/SDK テンプレートに Module architecture (overview) 章を追加 ([#128])

### Fixed

- Kotlin extractor annotation + Ktor path bugs ([#38])
- Redundant separator (`---`) at end of README ([#11])
- 内部ファイルパスが生成ドキュメントに漏れる問題 ([#134])
- merge-pr.sh CI pending/fail 誤判定 ([#124])
- tree-sitter core/grammar バージョン不整合 ([#125])
- coverage-check.py コードブロック行カウント ([#118])

### Docs

- New language/framework addition guide in CONTRIBUTING.md ([#34])
- Commit conventions, PR review, and release process docs added to docs/ ([#33])
- README: Branching strategy link and directory structure update
- テンプレートの章順設計原則を明文化 ([#129])
- Phase 2 WBS の章数ハードコードを修正 ([#126])

## [v0.7.0] - 2026-06-30

### Added

- `scripts/source_map_v2/` — role-typed, framework-aware, tree-sitter-based mechanical source map (schema 0.2.0)
- Per-language extractors for Python, TS/JS, Ruby/Rails, PHP, Java, C#, Go, SQL, COBOL
- Framework detection for source_map_v2
- Loud warnings instead of silent drops for unsupported languages
- Coexistence with v1 `source-map.py`

### Docs

- README: v0.7.0 status, Roadmap update, source_map_v2 directory structure

## [v0.6.0] - 2026-06-29

### Added

- Phase 0 bundle staging into `.specback/skill/`
- [REF:] placeholder consistency (no leading `L`)
- Variants/B — Context Optimization mode B reference variant

### Fixed

- Ruby top-level method extraction in source-map scripts
- Sources Read counter fix

## [v0.5.0] - 2026-06-15

### Added

- Mermaid styling contract (host-themed palette)
- `user_custom_deliverables` enforcement
- Strict `[REF: path:line]` format enforcement
- Phase 5 skip prevention
- Intent-vs-delivery audit
- Context Optimization mode B variant (`variants/B/`)

### Docs

- README: v0.5.0 status, Roadmap update, variants/ directory structure

## [v0.4.1] - 2026-06-11

### Changed

- Neutralized runtime-specific terminology for standalone use

## [v0.4.0] - 2026-06-09

### Added

- English-base migration of the entire skill bundle
- Bilingual output via `output_language` option
- English-first README structure
- English-base templates, references, scripts, and agent configurations

## [v0.3.0] - 2026-06-08

### Added

- Depth modes: comprehensive / outline / interactive
- Phase 6.5 interactive deep-dive mode
- `outline-tables.md` and outline-mode validations

## [v0.2.0] - 2026-06-06

### Added

- Chapter file naming convention enforcement
- Required files (3-file mandatory structure) validation
- Per-chapter sub-agent delegation
- Phase 4 loopback verification
- Granularity rules for spec generation
- Rails framework catalog
- Output-language selection
- French version support
- Framework-specific inventory units: Next.js, Expo, Flask, FastAPI
- Multi-language README (English + French added)

### Infrastructure

- Initial agent scripts and Phase 3/4/5 hardening
- Verification script with naming/required-file checks
- Project renamed: Claude Code Reverse Spec Generator

## [v0.1.0] - 2026-05-01

### Added

- Initial release of the Reverse Spec Generator (cc-rsg)
- Core Phase 1–7 pipeline structure
- Basic source-map extraction (v1)
- Question Bank (FAQ-based dialog refinement)
- Template-based specification document generation

[#1]: https://github.com/nekolife1984/specback/pull/1
[#2]: https://github.com/nekolife1984/specback/pull/2
[#10]: https://github.com/nekolife1984/specback/pull/10
[#11]: https://github.com/nekolife1984/specback/pull/11
[#12]: https://github.com/nekolife1984/specback/pull/12
[#13]: https://github.com/nekolife1984/specback/pull/13
[#14]: https://github.com/nekolife1984/specback/pull/14
[#18]: https://github.com/nekolife1984/specback/pull/18
[#32]: https://github.com/nekolife1984/specback/pull/32
[#33]: https://github.com/nekolife1984/specback/pull/33
[#34]: https://github.com/nekolife1984/specback/pull/34
[#35]: https://github.com/nekolife1984/specback/pull/35
[#36]: https://github.com/nekolife1984/specback/pull/36
[#37]: https://github.com/nekolife1984/specback/pull/37
[#38]: https://github.com/nekolife1984/specback/pull/38
[#42]: https://github.com/nekolife1984/specback/pull/42
[#51]: https://github.com/nekolife1984/specback/pull/51
[#52]: https://github.com/nekolife1984/specback/pull/52
[#53]: https://github.com/nekolife1984/specback/pull/53
[#55]: https://github.com/nekolife1984/specback/pull/55
[#57]: https://github.com/nekolife1984/specback/pull/57
