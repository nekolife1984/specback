# specback — Reverse Spec Generator

> A multi-agent skill that reverse-engineers specification documents from existing codebases

📖 **日本語版は下記にあります** — [Jump to Japanese →](#日本語版)

`specback` is a general-purpose framework for automatically generating specification documents — for maintenance engineers or end customers — from legacy or active codebases.

It is the **reverse direction** counterpart of `cc-sdd` (Spec Driven Development): while `cc-sdd` goes "spec → code", `specback` goes "code → spec".

---

## Why This Was Built

Legacy system modernization, codebase onboarding for new engineers, deliverable spec docs, internal knowledge consolidation — across all these scenarios, the problem of "we have the code but no reliable specification" is universal.

In the LLM era, asking an AI to "make a spec from this code" produces visually polished documents instantly. But in practice, if that document turns out to be "beautiful fiction filled with guesses", it breaks down in production.

`specback` prioritizes:

- **Honesty**: Don't hide guesses — mark them explicitly. Show "unresolved items" as a dedicated chapter
- **Traceability**: Every statement has a source code reference with line numbers
- **Completeness**: Enumerate all extractable units from the code, mechanically verify coverage
- **Progressive elaboration**: Recon → skeleton → chapter drafts → verify → dialog refine
- **Resumability**: Long sessions can be paused and resumed

---

## Design Heritage

`specback` is positioned as the latest generation in the following lineage:

- **KDM (Knowledge Discovery Metamodel, ISO/IEC 19506:2012)**: Language-neutral structured knowledge representation
- **OMG ADM (Architecture-Driven Modernization)**: MDRE (Model-Driven Reverse Engineering)
- **Siala & Lano (2025)**: LLM × MDRE empirical integration research
- **Reversa** (OSS): Modern form of "agent-readable executable specifications"
- **IBM watsonx Code Assistant for Z / AWS Transform / CAST Imaging**: "Deterministic graph + LLM natural language" hybrid architecture

`specback` builds on these by maximizing skill-based AI agent features (SKILL.md, subagents, AskUserQuestion, Task) into a general-purpose framework.

---

## Installation

specback uses the **Agent-driven workflow** — load the skill into your coding agent and follow the phase prompts:

1. `./install.sh --agent claude --level project` (or your preferred agent)
2. Navigate to your target codebase
3. Invoke the skill (`/specback` or equivalent)
4. Follow the agent's prompts through each phase

### Skill Stamp Install (CLI — new)

Stamp specback into an existing project directory with lockfile-based drift detection:

```bash
./install.sh /path/to/your-project
```

Options:
- `--check` — Drift detection (no changes made)
- `--force` — Overwrite stamped files (git commit first recommended)
- `--dry-run` — Show what would be stamped

See [docs/en/06-install-stamp.md](docs/en/06-install-stamp.md) or [docs/ja/06-install-stamp.md](docs/ja/06-install-stamp.md) for details.

### Quick Install (skill — no CLI needed)

Clone the repository and run the installer from the **project root directory** (not inside the repo):

```bash
git clone https://github.com/nekolife1984/specback.git
./install.sh
```

This interactive installer supports: Claude Code, Codex CLI, OpenCode, GitHub Copilot, Cursor, and Other agents.

Windows:
```powershell
git clone https://github.com/nekolife1984/specback.git
.\install.ps1
```

Dry-run mode:
```bash
./install.sh --dry-run
```

Install optional Python dependencies (tree-sitter grammars for precise source-code extraction):
```bash
./install.sh --install-deps
```

> **Note:** All specback scripts work with Python standard library only. The optional dependencies (`tree-sitter` + per-language grammars) enable fine-grained source-code analysis via `source_map_v2`. Without them, the system falls back to file-level units with a clear warning. See `scripts/requirements.txt` for the full list.

### Manual installation (example)

The installer above is recommended. To install manually, copy to your agent's skill directory:

```bash
# As a project-level skill (e.g. for Claude Code)
mkdir -p .claude/skills/
cp -r skills/specback .claude/skills/  # スキル（SKILL.md + phases/ アーカイブ）
cp -r scripts references schemas .claude/skills/specback/  # 共有アセット

# Or as a user-level skill
mkdir -p ~/.claude/skills/
cp -r skills/specback ~/.claude/skills/
cp -r scripts references schemas ~/.claude/skills/specback/
```

### Verify installation

Launch your agent and run `/help` — `specback` should appear in the skill list.

---

## Usage

### Basic Flow

```
1. Launch your coding agent at the target codebase root
2. Invoke the specback skill
3. Answer the 5-question goal definition (Phase 0)
4. Review recon results and pick a template (Phase 1)
5. Review the WBS and inventory (Phase 2)
6. Wait for parallel subagent investigation (Phase 3)
7. Review the verification report (Phase 4)
8. Refine the spec via Question Bank dialogue (Phase 5)
9. Receive the final deliverables (Phase 6)
```

### Pause and Resume

Even if you interrupt the session, progress is saved to `.specback/state.json`. On the next launch, a resume message appears with options: continue / rewind / full reset.

### Output Location

A `.specback/` directory is created at the root of the target project, containing:

```
.specback/
├── state.json              # Progress tracking
├── goal.json               # Phase 0 goal definition
├── recon-report.md         # Phase 1 reconnaissance
├── source-map.json         # Mechanical source unit map (v2)
├── inventory.json          # All inventory items
├── trace.json              # Spec-to-source traceability
├── wbs.json                # Work breakdown
├── questions.json          # Question Bank
├── knowledge-graph.jsonld  # JSON-LD Knowledge Graph (machine-queryable)
├── drafts/                 # Per-chapter drafts (intermediate, always in .specback/)
└── final/                  # Final deliverables (default) or {output_dir}/ if custom path set
```

Drafts always stay in `.specback/drafts/` regardless of the output directory choice. Final deliverables go to `{output_dir}/` (default: `.specback/final/`; custom: e.g. `docs/specs/`).

#### Version control

`goal.json` is the only file under `.specback/` that should be **committed to version control**: it records the Phase 0 goal definition and the chapter selection rationale (`customized_chapters`) — "why each chapter exists / does not exist" — which the delivered specs reference but do not fully duplicate. Everything else under `.specback/` (`state.json`, `drafts/`, `inventory.json`, `source-map.json`, `trace.json`, etc.) is intermediate state and should stay ignored.

Example `.gitignore` entry:

```gitignore
.specback/*
!.specback/goal.json
```

---

## Language

Starting **v0.4.0**, the entire skill bundle (`SKILL.md`, `agents/`, `templates/`, `references/`, and the docstrings/messages of `scripts/`) is **English-base**. The default for `goal.json.output_language` is `"en"`.

Japanese output is fully supported: select `日本語 (Japanese)` in Phase 0 Step 3, and the agent dynamically renders chapter bodies, AskUserQuestion bodies, and progress messages in Japanese while preserving every machine-readable element (`## Sources Read`, `<!-- REF: ... -->`, `<!-- CONFIDENCE: ... -->`, JSON keys, file slugs, ID prefixes) verbatim in English. See SKILL.md Principle #11 for the full contract.

---

## 6+1 Phase State Machine

| Phase | Name | Main Action |
|-------|------|-------------|
| 0 | Setup & Goal | 5-question goal definition (scope, reader, granularity), output language |
| 1 | Recon & Template | Shallow reconnaissance, template selection, **depth mode decision** |
| 2 | Plan & WBS | Skeleton generation, inventory extraction, WBS (branches on depth mode) |
| 3 | Investigate | Per-chapter independent sub-agent investigation (comprehensive: STEP A–G / outline: OUT-A–D) |
| 4 | Verify | Coverage, integrity, 11-item validation with loopback fixes |
| 5 | Refine via Dialogue | 3-stage (overview / critical clusters / individual) dialog to resolve uncertainty |
| 6 | Deliver | Output final deliverables to `.specback/final/` |
| **6.5** | **Interactive Deep-Dive** | (interactive mode only) On-demand deep-dive chapter generation guided by user |

See [`skills/specback/SKILL.md`](skills/specback/SKILL.md) for details.

---

## Depth Modes

Three depth modes are selectable at the end of Phase 1, based on codebase scale and reader purpose.

| Mode | Use Case | Chapter Body Format |
|------|----------|---------------------|
| **`comprehensive`** | Audit / regulatory compliance — full coverage required | Each chapter: 200+ lines, 10+ `<!-- REF: ... -->` markers, 1+ Mermaid diagram |
| **`outline`** (recommended default) | General use, large codebases | Enumerated tables of Modules / Entities / Actions / Data / Dependencies + Mermaid + deep-dive candidate lists |
| **`interactive`** | Team reference, iterative refinement | Same as outline + Phase 6.5 accepts user-directed deep-dives |

For codebases of 200 files or fewer, `comprehensive` is auto-selected. Above that threshold, the user is prompted to choose.

In `outline` / `interactive` modes, each table cell is mandatorily tagged with a **Confidence label** (🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED) to clearly distinguish guesses from confirmed facts. Deep-dive candidates are auto-selected based on 🔴 ASSUMED density, top-decile complexity, and business-critical keyword matches (auth / payment / permission / etc.).

---

## Supported Languages and Typical Units

`references/inventory-units.md` covers the following languages:

- PHP (Laravel / Symfony / CakePHP, etc.)
- COBOL (+ JCL)
- Python (Django / Flask / FastAPI, etc.)
- Java / Kotlin (Spring Boot, etc.)
- JavaScript / TypeScript (Express / Next.js / NestJS / Expo / React Native / React, etc.)
- C# (ASP.NET Core, etc.)
- Go
- **Ruby on Rails**: 14-unit catalog covering Controller / Model / Concern / Service / Job / Mailer / Helper / Lib / Migration / Route / View / JS module / config / Mailer template

Overview-table definitions for `outline` mode are in `references/outline-tables.md`, providing ripgrep-based exhaustive-enumeration patterns for 6 stacks: Ruby/Rails, Python/Django, JS/TS/React, Go, Java/Kotlin (Spring Boot).

Dedicated extraction guides are provided for major frameworks:

- **Flask**: Blueprints, view functions, hooks, Jinja2 templates, Flask-WTF forms, Flask-SQLAlchemy models, CLI commands
- **FastAPI**: APIRouter, Pydantic schemas, Dependencies, Background tasks, Middleware, Exception handlers, Security schemes
- **Next.js** (App Router / Pages Router): page / route / layout / Server Action / Middleware, with mixed-router support
- **Expo / React Native**: Screens, Navigators, native modules, `app.json` / `eas.json`, permissions, Managed / Bare Workflow detection

Inventory **granularity rules** are also built in: minimum count (`max(50, file_count // 20)`) and macro-unit ratio caps are mechanically enforced by the Phase 4 verification script.

### Mechanical source map v2 (role-typed)

`scripts/source_map_v2/` is a framework-aware, **tree-sitter-based** extractor (schema 0.2.0) that maps every unit onto the five universal tables (Modules / Entities / Actions / Data / Dependencies) and role-types it — `endpoint` (with HTTP method + path), `model`, `schema`, `component`, `job`, `route_group`, `migration`, `datastore`, … — across **9 languages**: Python, TypeScript/JavaScript, Ruby/Rails, PHP, Java, C#, Go, SQL, COBOL. Framework detection (FastAPI / Django / Flask / Rails / Laravel / Spring / Next.js / Express / NestJS, …) selects the right unit kinds. It coexists with the v1 `source-map.py` and is backward compatible. tree-sitter is an **optional** dependency; languages without a grammar fall back to file-level units with a loud warning (never a silent drop). Run it standalone:

```bash
python -m source_map_v2 --target <root> --output .specback/source-map.json
```

Unsupported languages or frameworks can be added on request via GitHub Issues.

---

## Templates

Initial set of 4 templates included:

- **Web Application Spec** (`templates/web-app.md`)
- **Batch System Spec** (`templates/batch-system.md`)
- **API Service Spec** (`templates/api-service.md`)
- **Library/SDK Spec** (`templates/library-sdk.md`)

Users can also bring their own templates.

---

## Question Bank

`specback` accumulates questions raised during investigation in `.specback/questions.json`.

### 7 Standard Categories

1. **business_rule**
2. **architecture_decision**
3. **data_model_intent**
4. **external_integration**
5. **naming_history**
6. **operational_requirement**
7. **security_compliance**

### Severity

- **critical**: Chapter cannot be written without resolving this
- **important**: Can be written by guess but with low confidence
- **nice-to-have**: Detail-level refinement

### Unanswerable Questions

Questions that will never get an answer ("the SME left the company", "no one remembers the historical context") are marked as `abandoned` and explicitly recorded in the "Unresolved Items" chapter of the final spec.

This is the foundation of the spec's trustworthiness.

---

## Directory Structure

```
specback/
├── README.md
├── LICENSE
├── .gitignore
└── skills/
    └── specback/
        ├── SKILL.md                         # Lightweight index (~90 lines)
        ├── phase-0-setup.md                 # Phase 0: Setup & Goal
        ├── phase-1-recon.md                 # Phase 1: Recon & Template
        ├── phase-2-wbs.md                   # Phase 2: Plan & WBS
        ├── phase-3-investigate.md           # Phase 3: Investigate
        ├── phase-4-verify.md                # Phase 4: Verify
        ├── phase-5-dialogue.md              # Phase 5: Refine via Dialogue
        ├── phase-6-deliver.md               # Phase 6: Deliver
        ├── phase-6-5-deepdive.md            # Phase 6.5: Interactive Deep-Dive
        ├── phase-7-drift.md                 # Phase 7: Drift Detection
        ├── phase-7b-ref-autofix.md          # Phase 7b: REF Auto-Fix
        ├── phase-7c-changespec.md           # Phase 7c: ChangeSpec
        ├── question-bank.md                 # Question Bank operation
        ├── subagent-behavior.md             # Sub-agent behaviour
        ├── state-management.md              # State management & resume
        ├── agents/
        │   └── chapter-investigator.md  # Per-chapter sub-agent definition
        ├── references/
        │   ├── inventory-units.md       # Language units + granularity rules + Rails catalog
        │   ├── outline-tables.md        # Overview-table definitions for outline mode (6 stacks)
        │   ├── template-catalog.md
        │   ├── question-categories.md
        │   ├── verification-checklists.md
        │   └── subagent-prompt.md
        ├── templates/
        │   ├── web-app.md
        │   ├── batch-system.md
        │   ├── api-service.md
        │   └── library-sdk.md
        ├── variants/
        │   └── B/                       # Optional Context Optimization mode B
        │       ├── README.md            # When and how to activate mode B
        │       ├── SKILL.phase3-stepG.md  # Phase 3 STEP G override
        │       └── chapter-investigator.md  # Mode-B sub-agent (return-value contract)
        └── scripts/
            ├── source-map.py            # Phase 2: source unit auto-extraction (v1)
            ├── source_map_v2/           # v2: role-typed, framework-aware, tree-sitter extractor (9 languages)
            │   ├── taxonomy.py          #   role vocabulary (5 universal tables)
            │   ├── model.py             #   source-map.json schema 0.2.0
            │   ├── detect.py            #   framework detection (layer 1)
            │   ├── pipeline.py          #   3-layer orchestrator
            │   ├── extractors/          #   per-language extractors (layer 2)
            │   └── tests/               #   acceptance tests
            ├── build-trace.py           # End of Phase 3 / Phase 4: build trace.json from <!-- REF: ... --> markers
            ├── build-traceability.py    # Phase 6: generate traceability.md
            └── coverage-check.py        # Phase 4: multi-item verification (comprehensive / outline modes)
```

---

## Status

Currently **v1.0.0** — initial stable release.

### API Stability

The following are considered stable and will not change without a MAJOR version bump:

- **Pipeline phases** (Phase 1–7) and their input/output contracts
- **`source_map_v2/` output schemas** (`source-map.json`, `inventory.json`)

---

## Preprint / Citation

The design rationale, intellectual lineage, and implementation decisions of this skill are detailed in the following preprint. Please cite when referring to this work in publications or talks.

> **Preprint**: https://zenodo.org/records/20541685

---

## License

MIT License. See [LICENSE](LICENSE).

---

## Contributing

Feedback, template requests, and bug reports are welcome via GitHub Issues.

Particularly welcome contributions:

- Inventory unit definitions for new languages/frameworks
- New templates (DWH, ML pipeline, IaC, mobile, etc.)
- Verification checklist additions
- Real-project application reports

---

## Related Projects

- **cc-sdd**: Spec Driven Development. The counterpart concept of `specback`
- **Reversa**: Similar OSS with a 5-phase pipeline

---

## Acknowledgments

The design draws significant inspiration from:

- The OMG community that standardized KDM (ISO/IEC 19506:2012)
- sandeco, the author of Reversa
- Siala & Lano (2025) "LLM4Models" paper
- Thoughtworks' review articles on AI-generated specifications

---

## Documentation

- [Branching Strategy](docs/en/01-branching-strategy.md)
- [Agent Roster — Phase-Specific Model Optimization](docs/en/05-agent-roster.md)

---
> "An honest spec with visible holes is more practically valuable than a polished spec full of fiction."
> — from the `specback` design principles
---

# 日本語版

# specback — Reverse Spec Generator

> 既存のコードベースから仕様書を逆生成(リバースエンジニアリング)するためのマルチエージェントスキル

📖 **English version is at the top** — [Jump to English →](#specback--reverse-spec-generator)

`specback` は、レガシーまたは現役のコードベースから、メンテナンス担当者あるいは納品先顧客に向けた仕様書を自動生成するための汎用フレームワークです。

「コード → 仕様」の **reverse 方向** を担うスキルであり、`cc-sdd`(Spec Driven Development、仕様駆動開発)の対概念として位置づけられています。

---

## なぜ作ったのか

レガシーシステムのモダナイゼーション、新規参画エンジニアによるコードベース理解、納品物としての仕様書整備、社内ナレッジ整備 — これらの場面で「コードはあるが仕様書がない / 信頼できない」という課題は普遍的です。

LLM時代になり、AIに「このコードから仕様書を作って」と頼むだけで一見綺麗な仕様書が生成されるようになりました。しかし実務では、その仕様書が「推測で埋められた美しいフィクション」だった場合、本番で破綻します。

`specback` は以下を最優先します。

- **正直さ**: 推測した部分は隠さず明示する。「未確定事項」を独立した章として示す
- **トレーサビリティ**: すべての記述にソースコードの行番号付き参照を付ける
- **抜け漏れ防止**: コードから抽出可能な単位を全件列挙し、機械的にカバレッジを検証する
- **段階的詳細化**: 偵察 → スケルトン → 章ドラフト → 検証 → 対話精緻化、と段階を踏む
- **再開可能性**: 長時間のセッションを中断・再開できる

---

## 設計の系譜

`specback` の設計は以下の系譜の最新世代として位置づけられます。

- **KDM(Knowledge Discovery Metamodel、ISO/IEC 19506:2012)**: 言語非依存の中立的な構造化知識表現
- **OMG ADM(Architecture-Driven Modernization)**: MDRE(Model-Driven Reverse Engineering)
- **Siala & Lano (2025)**: LLM × MDRE の統合実証研究
- **Reversa**(OSS): エージェント可読な実行可能仕様という現代的形態
- **IBM watsonx Code Assistant for Z / AWS Transform / CAST Imaging**: 「決定論的グラフ + LLM自然言語化」のハイブリッドアーキテクチャ

`specback` はこれらを踏まえ、スキルベース AI エージェントの機能(SKILL.md、subagents、AskUserQuestion、Task)を最大限活用したフレームワークとして設計されています。

---

## インストール

specbackは**エージェント駆動（スキル版）**のワークフローを使うよ：

| | エージェント駆動（スキル版） |
|---|---|
| **実行方法** | エージェントにスキルを読み込む |
| **CLI必要？** | ❌ 不要 |
| **おすすめ** | Copilot/Cursor/対話重視 |

日本語版はこちら: [スキルスタンプインストール](docs/ja/06-install-stamp.md)

### スキルスタンプインストール（CLI版・新機能）

ロックファイルベースのドリフト検出付きで、既存プロジェクトに specback をスタンプ:

```bash
./install.sh /path/to/your-project
```

オプション:
- `--check` — ドリフト検出（変更なし）
- `--force` — スタンプファイルを上書き（事前に git commit 推奨）
- `--dry-run` — スタンプ内容を表示

詳細は [docs/ja/06-install-stamp.md](docs/ja/06-install-stamp.md) を参照。

### クイックインストール（スキル版・CLI不要）

```bash
git clone https://github.com/nekolife1984/specback.git
./install.sh
```

この対話型インストーラーは Claude Code、Codex CLI、OpenCode、GitHub Copilot、Cursor など複数のエージェントに対応しています。

オプションの Python 依存パッケージ（tree-sitter 各言語パーサー）もインストールする場合:
```bash
./install.sh --install-deps
```

> **注意:** specback の全スクリプトは Python 標準ライブラリのみで動作します。オプション依存（`tree-sitter` + 各言語 grammar）は `source_map_v2` による精密なソースコード解析を可能にします。なくても file-level のユニットにフォールバックし、警告を表示します。詳細は `scripts/requirements.txt` を参照してください。

### 手動配置 (例)

インストーラーが使えない場合は、お使いのエージェントのスキルディレクトリにコピーしてください:

```bash
# 例: Claude Code のプロジェクトレベルスキルとして
mkdir -p .claude/skills/
cp -r skills/specback .claude/skills/  # スキル（SKILL.md + phases/ アーカイブ）
cp -r scripts references schemas .claude/skills/specback/  # 共有アセット
```

### 動作確認

エージェントを起動し、`/help` でスキル一覧に `specback` が表示されれば成功。

---

## 使い方

### 基本フロー

```
1. 対象コードベースのルートでエージェントを起動
2. specback スキルを呼び出す
3. ゴール定義5問に回答(Phase 0)
4. 偵察結果を確認しテンプレート選定(Phase 1)
5. WBS と インベントリをレビュー(Phase 2)
6. サブエージェントによる並列調査を待つ(Phase 3)
7. 検証レポートを確認(Phase 4)
8. Question Bank の対話で仕様を精緻化(Phase 5)
9. 最終成果物を受け取る(Phase 6)
```

### 中断と再開

セッションを中断しても、`.specback/state.json` に進捗が保存されます。次回エージェント起動時に再開メッセージが表示され、続きから / 巻き戻し / 全リセット のいずれかを選択できます。

### 出力場所

利用プロジェクトの直下に `.specback/` ディレクトリが作成され、以下が保存されます。

```{.specback/ tree}
.specback/
├── state.json              # 進捗管理
├── goal.json               # Phase 0 のゴール定義
├── recon-report.md         # Phase 1 の偵察結果
├── source-map.json         # 機械的なソースユニットマップ (v2)
├── inventory.json          # 全インベントリ項目
├── trace.json              # 仕様とソースのトレーサビリティ
├── wbs.json                # 作業分解
├── questions.json          # Question Bank
├── knowledge-graph.jsonld  # JSON-LD 知識グラフ (機械クエリ可能)
├── drafts/                 # 各章のドラフト（中間成果物、常に .specback/ 内）
└── final/                  # 最終成果物（デフォルト）／カスタムパス指定時は {output_dir}/
```

Drafts（中間ドラフト）は出力先に関わらず常に `.specback/drafts/` に配置されます。最終成果物は `{output_dir}/` に出力されます（デフォルト: `.specback/final/`、カスタム例: `docs/specs/`）。

#### バージョン管理 (Version control)

`.specback/` 配下で**コミット推奨なのは `goal.json` のみ**です。`goal.json` には Phase 0 のゴール定義と章の採用/除外判断（`customized_chapters`）—「各章が存在する／しない理由」— が記録されており、成果物（00-metadata.md 等）から参照される一方で完全には複製されません。それ以外（`state.json` / `drafts/` / `inventory.json` / `source-map.json` / `trace.json` 等）は中間状態なのでコミット対象外とします。

`.gitignore` の例:

```gitignore
.specback/*
!.specback/goal.json
```

---

## 言語 (Language)

**v0.4.0** から、スキル本体一式 (`SKILL.md` / `agents/` / `templates/` / `references/` / `scripts/` の docstring・メッセージ) は **英語ベース** になりました。`goal.json.output_language` のデフォルトは `"en"` です。

日本語出力は引き続き完全サポート: Phase 0 Step 3 で `日本語 (Japanese)` を選択すると、章本文・AskUserQuestion 質問文・進捗メッセージ等の自然言語出力が日本語で動的に生成されます。ただし機械可読要素 (`## Sources Read`、`<!-- REF: ... -->`、`<!-- CONFIDENCE: ... -->`、JSON キー、ファイル名 slug、ID prefix 等) は言語に関わらず英語固定です。詳細は SKILL.md の Principle #11 を参照。

---

## 6+1フェーズ状態マシン

| Phase | 名称 | 主な動作 |
|-------|------|---------|
| 0 | Setup & Goal | ゴール定義5問で対象範囲・読者・粒度を確定、出力言語選択 |
| 1 | Recon & Template | 浅い偵察を行い、仕様書テンプレートを選定、**depth モード判定** |
| 2 | Plan & WBS | スケルトン生成、インベントリ抽出、WBS分割(depth モードで章構成分岐) |
| 3 | Investigate | サブエージェントで各章を独立調査(comprehensive: STEP A〜G / outline: OUT-A〜D) |
| 4 | Verify | カバレッジ・整合性・11項目検証・ループバック修正 |
| 5 | Refine via Dialogue | 3段階(全体像/criticalクラスタ/個別)対話で不確実性を解消 |
| 6 | Deliver | 最終成果物を `.specback/final/` に出力 |
| **6.5** | **Interactive Deep-Dive** | (interactive モード時のみ) 利用者の指示で深掘り章を on-demand 生成 |

詳細は [`skills/specback/SKILL.md`](skills/specback/SKILL.md) を参照してください。

---

## Depth モード

対象コードベースの規模・読者用途に応じて、Phase 1 末尾で以下3つの深度モードから選択します。

| モード | 用途 | 章本文の形 |
|-------|------|----------|
| **`comprehensive`** | 監査・規制対応など完全網羅が必要な場合 | 各章 200 行以上、`<!-- REF: ... -->` 10件以上、Mermaid 1個以上 |
| **`outline`** (推奨デフォルト) | 通常用途、大規模コードベース | Modules / Entities / Actions / Data / Dependencies の **概観テーブル全列挙** + Mermaid + 深掘り候補リスト |
| **`interactive`** | チームで継続参照、対話的に詳細化 | outline と同じ + Phase 6.5 で利用者指示の深掘りを受付 |

200 ファイル以下のコードベースでは `comprehensive` が自動選択され、200 ファイル超では利用者に選択を促します。

`outline` / `interactive` モードでは、各表セルに **Confidence ラベル** (🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED) が必須付与され、推測と確認済みを明示的に区別します。深掘り候補は 🔴 ASSUMED の多い行、複雑度上位 10%、business-critical キーワード(auth / payment / permission 等) で自動選定されます。

---

## 対応言語と典型単位

`references/inventory-units.md` で以下の言語をカバーしています。

- PHP(Laravel / Symfony / CakePHP 等)
- COBOL(+ JCL)
- Python(Django / Flask / FastAPI 等)
- Java / Kotlin(Spring Boot 等)
- JavaScript / TypeScript(Express / Next.js / NestJS / Expo / React Native / React 等)
- C#(ASP.NET Core 等)
- Go
- **Ruby on Rails**: Controller / Model / Concern / Service / Job / Mailer / Helper / Lib / Migration / Route / View / JS module / config / Mailer template の14単位カタログ

`outline` モード用の概観テーブル定義は `references/outline-tables.md` にあり、Ruby/Rails、Python/Django、JS/TS/React、Go、Java/Kotlin(Spring Boot) の6言語について「どの ripgrep パターンで全列挙するか」を機械化しています。

主要フレームワークについては個別の抽出ガイドを用意しています。

- **Flask**: Blueprint、View function、Hook、Jinja2 テンプレート、Flask-WTF Form、Flask-SQLAlchemy Model、CLI コマンド
- **FastAPI**: APIRouter、Pydantic スキーマ、Dependency、Background Task、Middleware、Exception handler、Security scheme
- **Next.js**(App Router / Pages Router): page / route / layout / Server Action / Middleware、両 Router の混在対応
- **Expo / React Native**: Screen、Navigator、ネイティブモジュール、`app.json` / `eas.json`、パーミッション、Managed / Bare Workflow 判別

加えて、インベントリの **粒度規定** が組み込まれており、最低件数 (`max(50, file_count // 20)`)・マクロ単位禁止比率を Phase 4 検証で機械的にチェックします。

### 機械ソースマップ v2(役割型付き)

`scripts/source_map_v2/` は、フレームワーク対応・**tree-sitter ベース**の抽出器(schema 0.2.0)で、すべてのユニットを5つの普遍テーブル(Modules / Entities / Actions / Data / Dependencies)へ写像し、役割型付け(`endpoint`〔HTTP メソッド+パス付き〕/ `model` / `schema` / `component` / `job` / `route_group` / `migration` / `datastore` …)します。対応は **9言語**: Python、TypeScript/JavaScript、Ruby/Rails、PHP、Java、C#、Go、SQL、COBOL。フレームワーク検出(FastAPI / Django / Flask / Rails / Laravel / Spring / Next.js / Express / NestJS …)で適切なユニット種別を選びます。v1 `source-map.py` と並存し後方互換。tree-sitter は **オプション依存**で、grammar の無い言語はファイルレベル単位＋ loud warning にフォールバックします(黙殺しない)。単体実行:

```bash
python -m source_map_v2 --target <root> --output .specback/source-map.json
```

未対応言語・フレームワークは利用者要望で随時追加していきます(GitHub Issues)。

---

## テンプレート

初期セットとして以下4種類を同梱しています。

- **Webアプリケーション仕様書** (`templates/web-app.md`)
- **バッチ処理システム仕様書** (`templates/batch-system.md`)
- **APIサービス仕様書** (`templates/api-service.md`)
- **ライブラリ/SDK仕様書** (`templates/library-sdk.md`)

利用者が自前のテンプレートを持参することも可能です。

---

## Question Bank

`specback` は調査中に湧いた疑問を構造化して `.specback/questions.json` に蓄積します。

### 7標準カテゴリ

1. **business_rule**(業務ルール)
2. **architecture_decision**(アーキテクチャ判断)
3. **data_model_intent**(データモデル意図)
4. **external_integration**(外部システム連携)
5. **naming_history**(命名・歴史的経緯)
6. **operational_requirement**(運用要件)
7. **security_compliance**(セキュリティ・コンプライアンス)

### 深刻度

- **critical**: この疑問が解消されないと章が書けない
- **important**: 推測で書けるが、確度が低い
- **nice-to-have**: 細部の精緻化に関わる

### 回答不能な疑問

「SMEが退職した」「歴史的経緯を知る人がもういない」など永遠に答えが出ない疑問は `abandoned` としてマークし、最終仕様書の「未確定事項」章に明示的に記載します。

これは仕様書の信頼性を担保する根幹です。

---

## ディレクトリ構造

```
specback/
├── README.md
├── LICENSE
├── .gitignore
└── skills/
    └── specback/
        ├── SKILL.md                         # Lightweight index (~90 lines)
        ├── phase-0-setup.md                 # Phase 0: Setup & Goal
        ├── phase-1-recon.md                 # Phase 1: Recon & Template
        ├── phase-2-wbs.md                   # Phase 2: Plan & WBS
        ├── phase-3-investigate.md           # Phase 3: Investigate
        ├── phase-4-verify.md                # Phase 4: Verify
        ├── phase-5-dialogue.md              # Phase 5: Refine via Dialogue
        ├── phase-6-deliver.md               # Phase 6: Deliver
        ├── phase-6-5-deepdive.md            # Phase 6.5: Interactive Deep-Dive
        ├── phase-7-drift.md                 # Phase 7: Drift Detection
        ├── phase-7b-ref-autofix.md          # Phase 7b: REF Auto-Fix
        ├── phase-7c-changespec.md           # Phase 7c: ChangeSpec
        ├── question-bank.md                 # Question Bank operation
        ├── subagent-behavior.md             # Sub-agent behaviour
        ├── state-management.md              # State management & resume
        ├── agents/
        │   └── chapter-investigator.md  # 章単位サブエージェント定義
        ├── references/
        │   ├── inventory-units.md       # 言語別単位 + 粒度規定 + Rails カタログ
        │   ├── outline-tables.md        # outline モード用の概観テーブル定義(6言語)
        │   ├── template-catalog.md
        │   ├── question-categories.md
        │   ├── verification-checklists.md
        │   └── subagent-prompt.md
        ├── templates/
        │   ├── web-app.md
        │   ├── batch-system.md
        │   ├── api-service.md
        │   └── library-sdk.md
        ├── variants/
        │   └── B/                       # オプションの Context Optimization mode B
        │       ├── README.md            # mode B の使いどころと活性化方法
        │       ├── SKILL.phase3-stepG.md  # Phase 3 STEP G の上書き
        │       └── chapter-investigator.md  # mode B 用 sub-agent(return-value 契約)
        └── scripts/
            ├── source-map.py            # Phase 2: ソースユニット自動抽出 (v1)
            ├── source_map_v2/           # v2: 役割型付き・FW対応・tree-sitter 抽出器 (9言語)
            │   ├── taxonomy.py          #   役割語彙 (5普遍テーブル)
            │   ├── model.py             #   source-map.json schema 0.2.0
            │   ├── detect.py            #   フレームワーク検出 (第1層)
            │   ├── pipeline.py          #   三層オーケストレータ
            │   ├── extractors/          #   言語別エクストラクタ (第2層)
            │   └── tests/               #   受け入れテスト
            ├── build-trace.py           # Phase 3末/Phase 4: <!-- REF: ... --> からの trace.json 生成
            ├── build-traceability.py    # Phase 6: traceability.md 生成
            └── coverage-check.py        # Phase 4: 多項目検証(comprehensive / outline モード対応)
```

---

## 出版・引用情報

本スキルの設計思想・系譜・実装上の意思決定については以下のプレプリントに詳述しています。論文・発表で言及される場合は引用ください。

> **Preprint**: https://zenodo.org/records/20541685

---

## ライセンス

MIT License。詳細は [LICENSE](LICENSE) を参照。

---

|## Contributing
|
|利用フィードバック・テンプレート追加要望・バグ報告は [GitHub Issues](https://github.com/nekolife1984/specback/issues) にて受け付けます。
|
|特に以下の貢献を歓迎します。
|
|- 新しい言語・フレームワークのインベントリ単位定義
|- 新しいテンプレート(DWH、機械学習パイプライン、IaC、モバイルアプリ 等)
|- 検証チェックリストの拡充
|- 実プロジェクト適用例のレポート
|
|ブランチ戦略・開発フローについては以下を参照してください：
|
|- EN: [Branching Strategy](docs/en/01-branching-strategy.md)
|- EN: [Agent Roster](docs/en/05-agent-roster.md)
|- JA: [ブランチ戦略](docs/ja/01-branching-strategy.md)
|- JA: [Agent Roster](docs/ja/05-agent-roster.md)

---

## 関連プロジェクト

- **cc-sdd**: Spec Driven Development(仕様駆動開発)。`specback` の対概念
- **Reversa**: 類似OSS。5フェーズパイプライン

---

## 謝辞

設計思想にあたり、以下の先行研究・実装から多大な示唆を受けました。

- KDM(ISO/IEC 19506:2012)を策定した OMG コミュニティ
- Reversa の作者 sandeco 氏
- Siala & Lano (2025) "LLM4Models" 論文
- Thoughtworks の AI 仕様書生成に関するレビュー記事

---

> "綺麗で完成度の高い仕様書よりも、正直で穴が見えている仕様書のほうが実務的価値が高い。"
> — `specback` 設計原則より
