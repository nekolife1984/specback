# Skill Stamp — Install Experience

## Overview

The **skill stamp** install introduces an SSSF-inspired stamp separation for
specback installation. Instead of copying everything to a skill directory, it
creates a clean three-layer structure in the target project:

```
target-repo/
├── .specback_data/              ← Project-specific (user customizes)
│   ├── templates/                   → Custom templates (empty by default)
│   ├── prompt_engineering/          → Custom prompts (empty by default)
│   └── llockfile                    → Lockfile with install hashes
│
└── .claude/skills/specback/     ← Core skill (read-only stamp)
    ├── SKILL.md
    ├── phases/
    ├── scripts/
    ├── references/
    └── templates/
```

The `llockfile` (JSON lockfile) records SHA-256 hashes of every stamped file
at install time. This enables drift detection to alert you when stamped files
have been modified, added, or removed.

## Usage

### Basic Stamp

```bash
# From the specback repo root:
python3 scripts/specback_install.py /path/to/target-repo

# Or via the install.sh wrapper:
./install.sh /path/to/target-repo
```

### Drift Detection

```bash
python3 scripts/specback_install.py --check /path/to/target-repo

# Example output:
#   🔍  Drift check for: /path/to/target-repo
#      Installed: 2026-08-05T12:00:00Z
#      Version:   1.2.0
#   🔴  Modified files (1):
#        ~ .claude/skills/specback/SKILL.md
#   ✅  Intact files: 262
```

### Force Re-stamp

```bash
python3 scripts/specback_install.py --force /path/to/target-repo
```

> **Note:** It is recommended to `git commit` before `--force` to avoid
> losing any local customizations.

### Dry Run

```bash
python3 scripts/specback_install.py --dry-run /path/to/target-repo
```

Shows what would be stamped without writing any files.

### Help

```bash
python3 scripts/specback_install.py --help
```

## Lockfile

The lockfile lives at `.specback_data/llockfile` and is a JSON file:

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

The `hashes` section contains SHA-256 digests of all stamped files, keyed by
their relative path from the target root. The `--check` command compares these
against the current on-disk hashes.

## What Gets Stamped

| Layer | Source (specback repo) | Target | Re-stampable? |
|-------|----------------------|--------|:------------:|
| Core skill | `skills/specback/` | `target/.claude/skills/specback/` | Yes |
| Search skill | `skills/specback-search/` | `target/.claude/skills/specback-search/` | Yes |
| Shared assets | `scripts/`, `references/`, `schemas/`, `agents/`, `templates/`, `variants/` | `target/.claude/skills/specback/...` | Yes |
| Custom templates | (empty) | `target/.specback_data/templates/` | No (user data) |
| Custom prompts | (empty) | `target/.specback_data/prompt_engineering/` | No (user data) |

Files under `.specback_data/` are **never overwritten** on re-stamp — they are
your project-specific customizations.

## What is Excluded from the Stamp

Dev-only artifacts are **not** stamped into your project. These verification
and cache files belong to the specback repository's own test/CI environment,
not to the runtime skill, so they are skipped during install. The exclusion
set is shared and kept in sync across `scripts/specback_install.py`,
`install.sh`, and `install.ps1`:

| Category | Default exclusions |
|----------|--------------------|
| Test suites | `tests/` (any directory named `tests`) |
| Bytecode / caches | `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` |
| Internal state | `.specback/`, `graphify-out/` |
| Hidden entries | any file/dir whose name starts with `.` |
| Dev-only files | `dev-requirements.txt` |

Everything needed to **run** the skill is retained: every `.py` CLI in
`scripts/`, the `source_map_v2/` extractor module, `requirements.txt`
(runtime optional tree-sitter grammars), the `specback-search` MCP server,
and all `references/`, `templates/`, `schemas/`, `agents/`, `variants/`.
The `--check` / lockfile hashing uses the same exclusion, so "intact / drift"
counts stay consistent with what was actually stamped.

## Migration from Legacy Install

The `install.sh` wrapper detects stamp mode automatically:

- If called with a **path argument** (e.g. `/path/to/target`), it delegates to
  `scripts/specback_install.py`
- If called with legacy **`--agent`** or **`--level`** flags, it runs the original
  agent-install flow (compatible with Claude Code, OpenCode, Copilot, etc.)

Both modes coexist: you can use `./install.sh /repo` to stamp into a project
and `./install.sh --agent claude --level user` to install the skill to your
agent at the same time.

## Acceptance Criteria

- [x] `specback install /path` stamps files correctly
- [x] `.specback_data/` separates project data from stamped files
- [x] `--check` detects drift (modified/added/removed files)
- [x] Lockfile is created and verified on `--check`
- [x] `--force` overwrites stamped files
- [x] `--dry-run` shows what would be stamped
- [x] `install.sh` wrapper works in both stamp and legacy mode
- [x] Existing `install.sh` behavior is preserved (backward compatible)
- [x] Tests pass
