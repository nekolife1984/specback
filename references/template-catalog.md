# Template Catalog Reference

Selection guide used in Phase 1 when presenting template candidates to the user. For each template, this document defines the intended target, chapter-outline summary, selection criteria, and the decision-tree logic.

---

## Chapter ordering principles

The template chapter order **is** the final document order. It is designed around the **reader's comprehension flow** — what a reader needs to know first in order to understand what later:

| Position | Chapter group | What it answers | Typical chapters |
|:--------:|---------------|-----------------|------------------|
| 1 | Overview | What system is this? | Overview |
| 2 | Capability view | What can it do? | Feature specifications |
| 3 | Structural overview | How is it built, at a glance? | Architecture overview / Module architecture |
| 4 | Detail chapters | How does each part work? | Screens, routes/endpoints, data model, job catalogue, API catalogue, configuration, ... |
| 5 | Design rationale | Why is it shaped this way? | Design decisions (ADRs, module dependencies, cross-cutting concerns) |
| 6 | Boundaries | What can it not do? | Known constraints and unresolved items |

Rules:

1. **Structural overview goes early** — the Architecture / Module overview sits right after the capability view, so readers can orient themselves before reading details.
2. **Design rationale goes late** — Design decisions sits after the detail chapters, right before Known constraints. It deepens understanding of things the reader has already seen; placed early it would create forward references.
3. **Presentation order ≠ generation order** — the template defines the presentation order; Phase 3 may generate chapters in any order (it dispatches them in parallel). Keeping generation order aligned with presentation order is the current convention, but it is not a requirement.
4. **Judge additions and reorderings against this flow** — when adding or moving a chapter, ask "where does the reader's comprehension flow require this?" rather than "where was the last edit?".
5. **Chapter count is template-specific** — there is no fixed chapter count; each template defines its own outline. Phase docs and scripts must never hardcode a count.
6. **Reader-adaptive ordering** — each template defines a `reader_order` frontmatter mapping `primary_reader` types to an ordered chapter slug list. The default `maintenance_developer` order matches the template's native outline. `delivery_customer` moves installation/usage examples forward; `regulator` moves constraints/design decisions early. Phase 2 selects the matching order from `goal.json.primary_reader`.

---

## Initial set of 9

The skill ships with the following 9 templates by default. The user may also bring their own template (by specifying a path).

1. **Web application spec** (`templates/web-app.md`)
2. **Batch-system spec** (`templates/batch-system.md`)
3. **API service spec** (`templates/api-service.md`)
4. **Library / SDK spec** (`templates/library-sdk.md`)
5. **CLI tool spec** (`templates/cli-tool.md`)
6. **Infrastructure spec** (`templates/infrastructure.md`)
7. **Mobile app spec** (`templates/mobile-app.md`)
8. **Desktop app spec** (`templates/desktop-app.md`)
9. **Event-driven / Streaming spec** (`templates/event-driven.md`)
---

## 1. Web application spec

### Target
- Systems the user operates through screens.
- PHP (Laravel/Symfony/CakePHP), Python (Django/Flask), Ruby (Rails), Node.js (Next.js/Nuxt/Express), Java (Spring MVC), etc.
- Authentication, session management, and screen transitions are present.

### Chapter outline
- Overview / system purpose
- Feature specifications ← added (see references/outline-tables.md Feature grouping patterns)
- Architecture overview
- Screen list and transitions
- Routes / endpoint list
- Data model (ER diagram, entity definitions)
- Authentication and authorisation
- External-system integration
- Operations settings / deployment
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- Evidence of HTML rendering and a templating engine.
- Session-management code (`session`, `cookie`).
- Routing definitions present (`routes/`, `urls.py`, etc.).
- Existence of `views/`, `templates/`, `pages/` directories.

---

## 2. Batch-system spec

### Target
- Scheduled or event-driven background processing.
- COBOL + JCL, cron / systemd timers, Spring Batch, Apache Airflow, Celery, Sidekiq, AWS Batch / Lambda scheduled runs.
- Includes data pipelines (ETL).
### Chapter outline
- Overview / business purpose
- Feature specifications ← added (see references/outline-tables.md Feature grouping patterns)
- Architecture overview
- Job catalogue
- Triggers and schedule
- Data flow (input → processing → output)
- Error handling and retry policy
- Recovery procedures
- Operations calendar / dependency graph
- Monitoring / alerts
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- Presence of scheduler configuration (crontab, Quartz, Airflow DAG, JCL).
- Presence of job-execution scripts.
- No persistent UI or API, or only an admin one.
- Evidence of large-data processing (chunked processing, bulk operations).

---

## 3. API service spec

### Target
- Endpoints called by other systems.
- REST, GraphQL, gRPC, WebSocket.
- Microservices, public APIs, internal APIs.
### Chapter outline
- Overview / API purpose
- Feature specifications ← added (see references/outline-tables.md Feature grouping patterns)
- Architecture overview
- Endpoint catalogue
- Request / response specs (per endpoint)
- Error codes / error responses
- Authentication (API key, OAuth, JWT)
- Rate limiting / quotas
- Versioning
- SLA / performance requirements
- Operations settings
- Design decisions
- Known constraints and unresolved items
### Selection criteria
- Presence of OpenAPI / Swagger / GraphQL schema.
- Routing definitions centred on endpoints (`/api/...`).
- No web UI (HTML rendering), or only as a secondary feature.
- Presence of API-Gateway configuration (Kong, AWS API Gateway, etc.).

---

## 4. Library / SDK spec

### Target
- Reusable code consumed by other applications.
- npm / pip / composer / gem / NuGet packages.
- Internal common libraries.

### Chapter outline
- Overview / library purpose
- Feature specifications ← added (see references/outline-tables.md Feature grouping patterns)
- Module architecture (overview) ← top-level structure, placed early for orientation (see Chapter ordering principles)
- Installation
- Public API catalogue
- Usage examples (quick start)
- Configuration options
- Compatibility (supported language versions, dependencies)
- Extension points / plugin system
- Migration guide (from older versions)
- Internal structure (optional)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- Package manifest (`package.json` / `setup.py` / `composer.json`, etc.) defines `name`, `version`, `main` / `module`.
- Directory structure consistent with distribution (`dist/`, `lib/`, `src/`).
- No application-entry code (a main function, entry-point script), or only samples.

---

## 5. CLI tool spec

### Target
- Terminal-based tools with a CLI entry point (typer/click/argparse, cobra, clap, commander).
- Console scripts, `package.json` `bin` entries, `[[bin]]` tables in Cargo.toml.
- Tools primarily operated via terminal commands, pipes, and exit codes.

### Chapter outline
- Overview / system purpose
- Feature specifications
- Module architecture
- Installation (pip / brew / pipx / cargo install / npm install -g)
- Command catalogue (all subcommands, arguments, options, exit codes)
- Usage examples (CRUD workflows, pipe chaining, error cases)
- Configuration (file paths, environment variables, config files)
- Output format (stdout, stderr, machine-readable --json, colour, pagination)
- Internal structure (optional)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- `[project.scripts]` / `console_scripts` entry in Python packaging metadata.
- `package.json` → `"bin"` field for Node.js packages.
- `[[bin]]` table in `Cargo.toml` for Rust.
- `package main` + `main()` + argument-parsing library (cobra, urfave/cli) for Go.
- Import of typer / click / argparse / commander / cobra / clap.
- No web framework (flask/django/express/fastapi/spring) import as primary interface.
- No `app.run` / `server.listen` / `uvicorn.run`.

---

## 6. Infrastructure spec

### Target
- Infrastructure-as-Code projects (Terraform, CloudFormation, CDK, Pulumi, Kubernetes manifests).
- Cloud resource definitions and deployment configurations.
- IaC-dominant repositories with little to no application code.

### Chapter outline
- Overview (system purpose, cloud provider, account structure, high-level architecture diagram)
- Feature specifications
- Resource inventory (all resources: compute, storage, network, security)
- Network topology (VPC, subnets, routing, DNS, CDN, NAT, VPN, Direct Connect)
- Deployment pipeline (CI/CD configuration, deployment strategy, GitOps)
- Configuration and environment (dev/staging/prod, parameter store, secret management, env vars)
- Monitoring and observability (metrics, log aggregation, alerts, dashboards, SLA/SLO)
- Disaster recovery and backup (RTO/RPO, backup strategy, failover, DR test procedures)
- Cost and sizing (resource sizing, cost estimates, reserved instances, tagging strategy)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- Presence of `*.tf` / `*.tfvars` / `versions.tf` / `terraform {` (Terraform).
- `AWSTemplateFormatVersion` or `Resources:` top-level key in YAML/JSON (CloudFormation).
- `aws-cdk-lib` / `cdk.json` / `stack.ts` / `stack.py` (CDK).
- `Pulumi.yaml` / `@pulumi/*` imports (Pulumi).
- `apiVersion:` / `kind:` / `metadata:` / `spec:` in YAML manifests (Kubernetes).
- `Dockerfile` / `docker-compose.yml` (Docker).
- IaC files represent >50% of total project files and application code is minimal.

---

## 7. Mobile app spec

### Target
- iOS (Swift), Android (Kotlin), Flutter, React Native, Kotlin Multiplatform apps.
- Apps that run on mobile platforms with platform-specific APIs, offline support, and store deployment.

### Chapter outline
- Overview (app purpose, target platform, minimum OS version)
- Feature specifications
- Module architecture (layer diagram: Presentation/Domain/Data)
- Screen list and transitions (navigation stack, tabs, modals, deep links)
- State management (Riverpod/Redux/Bloc/ViewModel + State, global vs. local state)
- Data persistence and offline-first (local DB, cache strategy, offline sync, conflict resolution)
- Platform API integration (camera, GPS, biometrics, push notifications, file access, sensors)
- Push notifications (APNs/FCM config, notification types, payload structure, tap behaviour)
- Networking and sync (API communication layer, cache interceptors, background sync, WebSocket)
- Build and deployment (code signing, provisioning profiles, store publishing, CI/CD, TestFlight/Internal Track)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- `.xcodeproj` / `.xcworkspace` / `Info.plist` / `@main` / `AppDelegate` (iOS/Swift).
- `build.gradle.kts` / `AndroidManifest.xml` / `MainActivity` (Android/Kotlin).
- `pubspec.yaml` + `flutter:` section + `main.dart` (Flutter).
- `package.json` + `react-native` dependency + `index.js`/`App.tsx` (React Native).
- `build.gradle.kts` + `kotlin { android() ios() }` (Kotlin Multiplatform).
- Primary artifact is a mobile app (no web server as the main component).

---

## 8. Desktop app spec

### Target
- Electron, Tauri, Qt (C++/Python), WinForms, WPF, SwiftUI (macOS) desktop applications.
- Apps with window management, system tray, native file access, and installer distribution.

### Chapter outline
- Overview (app purpose, target OS, minimum requirements)
- Feature specifications
- Module architecture (process model: main process / renderer process, etc.)
- Window management and menus (window listing, menu bar, context menus, tab/dock/taskbar integration)
- UI component catalogue (main UI components, custom controls, theme system)
- Platform integration (file system access, native dialogs, clipboard, drag-and-drop, dock/task tray, Spotlight)
- State management and persistence (local settings, session management, cache)
- Auto-update and installer (installer methods, auto-update mechanism, code signing)
- Networking (API communication, WebSocket, local server, P2P, LAN discovery)
- Keyboard shortcuts and accessibility (global shortcuts, VoiceOver/Narrator/Orca, focus management)
- Build and deployment (packaging, code signing, CI/CD, distribution channels)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- `package.json` + `electron` dependency + `main` as `.js`/`.ts` + `electron-builder`/`electron-forge` config (Electron).
- `tauri.conf.json` / `Cargo.toml` + `tauri` dependency / `src-tauri/` directory (Tauri).
- `.pro` file / `CMakeLists.txt` + `find_package(Qt...)` / `QApplication` / `QMainWindow` (Qt C++).
- `PyQt`/`PySide` import / `.ui` files (Qt Python).
- `.csproj` + `UseWindowsForms`/`UseWPF` / `Form`/`Window` inheritance (WinForms/WPF).
- `@main struct App: App` / `WindowGroup` / `Info.plist` + `LSUIElement` (SwiftUI macOS).
- No web server code as the primary interface.
- No mobile platform targeting as the primary interface.

---

## 9. Event-driven / Streaming spec

### Target
- Asynchronous messaging systems using a message broker or event bus.
- Kafka, Pulsar, AWS EventBridge, SQS, SNS, RabbitMQ, Google Pub/Sub, Azure Event Hubs.
- Event sourcing, CQRS, stream processing, pub/sub architectures.
- Both real-time streaming and queued message processing.

### Chapter outline
- Overview (system purpose, event-driven rationale, topology overview)
- Feature specifications
- Module architecture (producer/consumer layout, module composition)
- Event catalogue (all event types, schemas, versioning, compatibility policy)
- Producers (producer mapping, trigger conditions, payload structure, partitioning key strategy)
- Consumers (consumer mapping, consumer groups, processing logic, offset management, idempotency)
- Serialization and schema (Avro/Protobuf/JSON, schema registry, compatibility policies, schema evolution)
- Delivery guarantees (at-least-once / exactly-once / at-most-once, DLQ, retry policy, idempotent producer)
- Partitioning and scaling (partition key design, partition count, rebalancing strategy, throughput)
- Error handling and recovery (circuit breaker, replay procedures, backpressure, poison pill messages)
- Monitoring and observability (lag monitoring, throughput metrics, consumer lag, offset management, alerting)
- Design decisions
- Known constraints and unresolved items

### Selection criteria
- Configuration for Kafka (`kafka-clients`, `spring-kafka`, `confluent_kafka`, Docker Compose with `cp-kafka`).
- Configuration for Pulsar (`pulsar-client`, `pulsar://` URL).
- AWS EventBridge resources (`aws-cdk-lib/aws-events`, `EventBus`, `PutEvents`).
- AWS SQS/SNS resources (`aws-cdk-lib/aws-sqs`, `aws-cdk-lib/aws-sns`, `boto3` SQS client).
- RabbitMQ configuration (`amqp`, `pika`, `spring-amqp`, Docker Compose with `rabbitmq`).
- Google Pub/Sub client library (`google-cloud-pubsub`).
- Azure Event Hubs client library (`azure-eventhub`).
- Presence of producer/consumer/publisher/subscriber directories or class names.
- Event/Message class inheritance trees.
- Primary architecture is event-driven (async pub/sub), not sync request-response.

---

## Decision tree (template recommendation logic)

Based on the Phase 1 reconnaissance, the agent follows this procedure to recommend a template:

```text
1. Does the package manifest define main/module/bin?
   YES → Is there application-startup code?
            NO  → Recommend Library / SDK spec
            YES → Continue

1b. Does the project define a CLI entry point?
    YES → Is argument-parsing code present (typer/click/argparse/commander/cobra/clap)?
             YES → Is the primary interface terminal (no web server, no HTML rendering)?
                      YES → Recommend CLI tool spec
                      NO  → Continue
             NO  → Continue

1c. Is the project focused on IaC / infrastructure?
    YES → Are there Terraform / CloudFormation / CDK / Pulumi / K8s manifests?
             YES → Is there NO application code (no main/web/server entry points)?
                      YES → Recommend Infrastructure spec
                      NO  → Recommend composite (infrastructure primary + app secondary)
             NO  → Continue

1d. Does the project target mobile platforms?
    YES → Are there iOS/Android/Flutter/React Native project files?
             YES → Is the primary artifact a mobile app (no web server)?
                      YES → Recommend Mobile app spec
                      NO  → Recommend composite (mobile + API)
             NO  → Continue

1e. Does the project target desktop platforms?
    YES → Are there Electron / Tauri / Qt / WinForms / WPF / SwiftUI project files?
             YES → Is the primary artifact a desktop app (windowed UI, not web server)?
                      YES → Recommend Desktop app spec
                      NO  → Continue
             NO  → Continue

1f. Does the project use a message broker / event bus?
    YES → Are there Kafka / Pulsar / EventBridge / SQS / SNS / RabbitMQ / Pub/Sub configurations?
             YES → Is the primary architecture event-driven (async pub/sub, not sync request-response)?
                      YES → Recommend Event-driven / Streaming spec
                      NO  → Continue (likely composite with api-service)
             NO  → Continue

2. Do routing definitions exist?
   YES → Is there HTML rendering (views/templates)?
            YES → Recommend Web application spec
            NO  → Recommend API service spec

3. Are scheduler configuration / batch scripts the main subject?
   YES → Recommend Batch-system spec

4. None of the above / composite type
   → Present multiple candidates and ask the user.
   → Example: "Includes both web app and API; recommend a merged custom outline."
```

---

## Handling composite projects

Real projects often do not fit into a single template. Handle them as follows.

### Overview: composite detection

When Phase 1 reconnaissance detects characteristics of multiple templates (e.g. both screens and endpoints, or a desktop app with an API backend), the agent must:

1. Identify which template types are present (web-app, api-service, desktop-app, mobile-app, cli-tool, batch-system, library-sdk, infrastructure).
2. Determine the relationship: primary/secondary, equal-scale composite, or separate services.
3. Classify the composite architecture pattern (see below).
4. Apply the recommended approach — unified spec, separate specs with cross-references, or extended monorepo handling.

### When there is a primary / secondary relationship
- Pick the primary template and add a chapter from the secondary one.
- Example: web app primary, batch secondary → add a "background jobs" chapter to the web-app spec.

### Composite at equal scale
- Generate a custom template by merging the chapter outlines.
- Ask the user for the chapter-ordering preference.

### Monorepo with multiple services
- Recommend generating separate specs per service.
- Merge into a single spec only if the user explicitly wants one spec for the whole monorepo.

---

### Pattern 1: Client-Server (e.g. Desktop App + API Service, Mobile App + API Backend)

**Architecture:** Client side + Server side + Communication layer

**Recommended approach:** Unified spec for tighter coupling; separate specs with cross-references for loosely coupled teams.

#### Unified spec chapter ordering

| # | Chapter | Source template | Notes |
|---|---------|----------------|-------|
| 1 | Overview | Common (merged from both) | System purpose, intended users, scope |
| 2 | Feature specifications | Common (Client + Server features merged) | Feature catalogue covering both sides |
| 3 | **System architecture** | **Composite common chapter** | Client-Server topology, tier data flow, deployment diagram |
| 4 | **API contract** | **Composite common chapter** | Full API list, request/response schemas, auth methods, versioning |
| 5 | Client: UI / Screen list | desktop-app or mobile-app | Screen list, transitions, navigation |
| 6 | Client: Platform integration | desktop-app or mobile-app | OS integration, notifications, background tasks |
| 7 | Server: Endpoint catalogue | api-service | Full endpoint list with methods and paths |
| 8 | Server: Data model | api-service | ER diagram, entity definitions |
| 9 | Server: Auth | api-service | Authentication flows, token management |
| 10 | Client: State / Data persistence | desktop-app or mobile-app | Local storage, caching, sync strategy |
| 11 | Design decisions | Both (merged) | Architectural decisions for both sides |
| 12 | Known constraints | Common | Cross-cutting constraints |

#### Separate specs with cross-references

```markdown
Client spec:
  - "API contract details are in the Server spec Chapter 7 (Endpoint catalogue)"
  - REF: server/specs/07-endpoint-catalogue.md

Server spec:
  - "Screen transitions are in the Client spec Chapter 5 (Screen list)"
  - REF: client/specs/05-screen-list.md
```

#### Selection criteria
- Evidence of two distinct deployable units (e.g. separate `package.json`, `Dockerfile`, deployment configs).
- Client has UI code; Server has endpoint/routing code.
- A communication protocol boundary (HTTP, WebSocket, gRPC) is identifiable.

---

### Pattern 2: 3-Tier (Presentation + Application + Data)

**Architecture:** Presentation tier (UI) + Application tier (business logic / API) + Data tier (persistence / storage)

**Recommended approach:** Single unified spec (tier-spanning consistency is more important than separation).

#### Unified spec chapter ordering

| # | Chapter | Content | Notes |
|---|---------|---------|-------|
| 1 | Overview | Overall purpose, 3-tier responsibilities | |
| 2 | Feature specifications | Tier-spanning feature list | Features may span multiple tiers |
| 3 | **System architecture** | **Composite common chapter** | 3-tier topology diagram, tier interfaces, deployment configuration |
| 4 | Presentation tier: UI | Screen list, transitions (web-app / mobile-app / desktop-app) | |
| 5 | Application tier: API / Logic | Endpoint catalogue, business rules (api-service) | |
| 6 | **Data flow (cross-tier)** | **Composite common chapter** | Presentation → Application → Data flow, caching, sync/async |
| 7 | Data tier: Data model | ER diagram, entity definitions, schema | |
| 8 | Auth (cross-tier) | Consistent auth flow: token → session → DB | Covers auth across all tiers |
| 9 | Operations / Deployment | Deployment per tier, CI/CD, scaling | |
| 10 | Design decisions | Technology choices per tier, why 3 tiers | Including tier-separation rationale |
| 11 | Known constraints | Constraints per tier | |

#### Selection criteria
- Three clearly separated layers (UI code, business logic code, data access code) in the codebase structure.
- Each tier may correspond to a separate deployment unit or be logical layers within the same process.
- Separation of concerns is a deliberate architectural choice (not accidental).

---

### Pattern 3: Monorepo with shared library

Extends the base "Monorepo with multiple services" handling when services share a common library.

**Architecture:** Multiple services + shared library(s)

**Recommended approach:** Separate specs per service + one shared library spec. Cross-reference shared library spec from each service spec.

#### Additional guidelines

| Aspect | Action |
|--------|--------|
| Shared library spec | Generate a full Library / SDK spec for each shared library |
| Service-to-library dependency | List in each service spec's dependency section: "depends on `shared-lib` vX.Y.Z" |
| Version alignment | Document version pinning strategy (monorepo-sync, semver ranges, lockfile) |
| API contract | If the library exposes a public API, add an "API catalogue" chapter (from library-sdk template) |
| Cross-reference pattern | `REF: shared-lib/specs/04-api-catalogue.md` in service specs |

#### Spec document layout

```
project/
├── service-a/
│   └── specs/service-a-spec.md   ← api-service or web-app spec
├── service-b/
│   └── specs/service-b-spec.md   ← api-service or web-app spec
└── shared-lib/
    └── specs/shared-lib-spec.md  ← library-sdk spec
```

#### Selection criteria
- Multiple services sharing code under a common root (`packages/`, `lib/`, `common/` directories).
- Package manifest dependencies between service and library.
- Shared code is packaged as a distributable unit or internal module.

---

### Composite common chapters

The following chapters appear across multiple composite patterns. They are defined as independent reference templates in `references/composite-chapters/` and reused by the agent when generating composite specs.

| Chapter | Description | Reference file |
|---------|-------------|----------------|
| System architecture | Overall system topology, tier interfaces, deployment diagram, inter-component data flow | `references/composite-chapters/01-system-architecture.md` |
| API contract | Client↔Server full API list, request/response schemas, auth methods, versioning strategy | `references/composite-chapters/02-api-contract.md` |
| Data flow (cross-tier) | Tier-spanning data flow, caching strategy, sync vs. async communication, data consistency | `references/composite-chapters/03-data-flow.md` |

---

## When the user brings their own template

1. Get the path to the template file.
2. Parse the template and extract the chapter outline.
3. Check whether each chapter has a meta-comment describing what it covers.
   - When missing, the agent infers it from the chapter title and confirms with the user.
4. Use the extracted outline for Phase 2 skeleton generation.

---

## When the user adjusts the recommendation

After the user accepts the recommendation, accept chapter additions, removals, or renames.

```
Agent: "I recommend the Web application spec. The outline is:
- Overview
- Architecture
- Screen list
- Routes
- Data model
- Authentication and authorisation
- External integration
- Operations settings

Any chapters to add, remove, or rename?"

User: "Add a 'non-functional requirements' chapter. Place it before 'Operations settings'."

Agent: "Got it. Finalising with:
- Overview
- Architecture
- Screen list
- Routes
- Data model
- Authentication and authorisation
- External integration
- Non-functional requirements   ← added
- Operations settings"
```

---

## Detection rules (chapter-level code analysis)

Phase 1 でテンプレート選択後、コードベースを分析して章構成を自動カスタマイズするためのルール定義。各テンプレートの frontmatter `detection_rules` に定義され、Phase 1 エージェントがこれを読み取って検出を実行する。

> **機械可読版**: `templates/catalog.json` に各テンプレートの `detection_rules`（`always_include` / `chapters` / `extra_chapters` / `optional` の ID リスト）が登録されている。テンプレート frontmatter と catalog は `scripts/validate-template-catalog.py` で同期検証される。プログラムから参照する場合は catalog を、人が読む場合はテンプレート frontmatter を使う。

### Detection types

| Type | Method | Example |
|:-----|:-------|:--------|
| `dirs` | `glob <dir>/**` でディレクトリ存在確認 | `views/`, `templates/` |
| `files` | `glob <pattern>` でファイル存在確認 | `routes/**`, `Dockerfile` |
| `patterns.rgs` | `rg <pattern>` でコード内キーワード検索 | `session`, `HttpClient` |
| `patterns.deps` | 依存関係ファイル（package.json, Gemfile等）の該当パッケージ確認 | `devise`, `celery` |
| `patterns.files` | `glob <pattern>` で特定ファイル検索 | `**/middleware/**auth*` |

### Decision outcomes

| Outcome | Icon | Action |
|:--------|:----:|:-------|
| Detected (code found) | ✅ | Include the chapter as-is |
| Not detected, non-optional | ❌ | Exclude; user may restore |
| Not detected, optional | ⚠️ | Include but mark as optional |
| Extra chapter detected | ➕ | Auto-add after the `insert_after` anchor |
| Merge triggered | 🔗 | Merge N chapters into one |
| Split triggered | 🔀 | Split one chapter into N |

### Rule format reference

```yaml
detection_rules:
  always_include:                              # 常に含める章（概要、設計判断など）
    - ch-overview

  chapters:                                    # 標準テンプレートの章ごとの検出ルール
    - id: ch-auth
      title: Authentication and authorisation
      slug: 08-authentication-authorisation     # reader_orderの参照slug
      detection:
        dirs: [...]                            # ディレクトリ存在確認
        files: [...]                           # ファイル存在確認
        patterns:                              # 複合パターン（いずれかマッチで ✅）
          - rgs: ["pattern1", "pattern2"]       # grep検索
          - deps: ["gem", "package"]            # 依存パッケージ
          - files: ["glob/**pattern"]           # ファイルglob検索
        note_missing: "..."                    # 未検出時の表示メッセージ
        optional: true                         # true: 未検出でも optional として含める

  extra_chapters:                              # 自動追加候補
    - id: ch-background-jobs
      title: Background jobs
      slug: 13-background-jobs
      detection:
        dirs: [...]
        deps: ["sidekiq", "celery"]
      insert_after: ch-external-interfaces     # 挿入位置
      note_detected: "..."

  granularity:                                 # 統合・分割ルール
    merge:                                     # コードが少ない場合の統合
      - key: screens_routes
        when: { screens_max: 3, routes_max: 10 }
        chapters: [ch-screens, ch-routes]
        into_title: "Web interface (screens & routes)"
        note: "..."
    split:                                     # コードが多い場合の分割
      - key: data_model_large
        when: { entities_min: 20 }
        chapter: ch-data-model
        into:
          - { id: ch-data-model-core, title: "Data model (core entities)" }
          - { id: ch-data-model-analytics, title: "Data model (analytics/reporting)" }
        note: "..."
```

### Per-template detection rules

各テンプレートの `detection_rules` は該当テンプレートファイルの frontmatter に定義されている。

| Template | File | Chapters with detection |
|:---------|:-----|:-----------------------|
| web-app | `templates/web-app.md` | All 12 standard chapters + 2 extra chapters + merge/split rules |
| api-service | `templates/api-service.md` | All 12 standard chapters + 1 extra chapter + merge/split rules |
| batch-system | `templates/batch-system.md` | All 12 standard chapters + 1 extra chapter + merge/split rules |
| library-sdk | `templates/library-sdk.md` | All 11 standard chapters + 1 extra chapter + merge/split rules |
| cli-tool | `templates/cli-tool.md` | 11 standard chapters + 6 detection chapters + 2 extra chapters + merge/split rules |
| infrastructure | `templates/infrastructure.md` | 11 standard chapters + 6 detection chapters + 2 extra chapters + merge/split rules |
| mobile-app | `templates/mobile-app.md` | 12 standard chapters + 7 detection chapters + 2 extra chapters + merge/split rules |
| desktop-app | `templates/desktop-app.md` | 13 standard chapters + 8 detection chapters + 2 extra chapters + merge/split rules |
| event-driven | `templates/event-driven.md` | 13 standard chapters + 9 detection chapters + 2 extra chapters + merge/split rules |

### Output format (goal.json.customized_chapters)

```json
{
  "customized_chapters": [
    {"id": "ch-overview", "title": "Overview", "status": "included", "note": null, "confidence": "always", "review_note": null},
    {"id": "ch-auth", "title": "Authentication and authorisation", "status": "excluded", "note": "認証フレームワーク・認証関連コードが見つかりませんでした", "confidence": "high", "review_note": null},
    {"id": "ch-background-jobs", "title": "Background jobs", "status": "auto_added", "note": "Sidekiq設定検出", "confidence": "high", "review_note": null},
    {"id": "ch-data-model", "title": "Data model", "status": "included", "note": "optional: データモデル定義未確認", "confidence": "low", "review_note": null}
  ],
  "chapter_actions_applied": {
    "excluded": ["ch-auth"],
    "auto_added": ["ch-background-jobs"],
    "merged": [],
    "split": []
  }
}
```

#### review_note field (🆕)

Added in Phase 1 Step 3d (Template fit critical review). Records structural changes
beyond detection_rules toggles:

| Value | Meaning |
|:------|:--------|
| `null` | No structural change. Detection_rules result stands. |
| `"... → moved to position N"` | Chapter was reordered based on recon evidence. |
| `"... → merged into ..."` | Chapters merged beyond detection_rules merge rules. |
| `"... → split: ..."` | Chapters split beyond detection_rules split rules. |
| `"Custom structure — no applicable template"` | Built from scratch (⚪ No template mode). |

## Template version management

Each template file starts with version information.

```yaml
---
template_name: web-app
template_version: 0.1.0
last_updated: 2026-05-01
---
```

The consuming project's `wbs.json` records the template version, guaranteeing reproducibility.

---

## Future templates

After OSS release, the following templates may be added in response to user requests:

- Data warehouse / DWH spec
- Machine-learning pipeline spec
- Blockchain / smart-contract spec
- Game-design spec

Requests are received via GitHub Issues.
