# Branching Strategy — GitHub Flow

## Overview

This project uses **GitHub Flow** — a lightweight, branch-based workflow with a single permanent branch (`main`) and short-lived feature branches.

> GitHub Flow is chosen because:
> - Single developer — minimal ceremony
> - Aligns with existing conventions (PR mandatory, squash merge, one-change-one-commit)

## Permanent Branches

| Branch | Protection | Purpose |
|--------|-----------|---------|
| `main` | ✅ No direct push | Single source of truth. All changes arrive via PR. |

## Branch Naming Conventions

All work branches branch from `main` and are deleted after merge.

| Prefix | Example | When to use |
|--------|---------|-------------|
| `feat/<kebab-case>` | `feat/add-plantuml-template` | New feature or enhancement |
| `fix/<kebab-case>` | `fix/detect-py-encoding` | Bug fix |
| `chore/<kebab-case>` | `chore/update-deps` | CI, maintenance, refactoring, dependencies |
| `docs/<kebab-case>` | `docs/branching-strategy` | Documentation only |

## PR Lifecycle

```
main → feat/xxx → commits → open PR → CI (pytest + pyrefly + trace gates)
                                    ↓
                          All green? → squash merge to main → delete branch
                                    ↓
                          Failed? → fix & push → CI re-runs
```

### Rules

1. **Branch from the latest `main`** — rebase if behind
2. **Short-lived branches** — hours to days, never weeks
3. **One logical change per branch** — corresponds to one conventional commit
4. **Squash merge** — keeps `main` history linear and clean
5. **Conventional commit message on merge** — `feat: description (#N)`
6. **Delete branch after merge** — both remote and local

### Direct Push Exceptions

| Change type | Direct push? | Condition |
|-------------|-------------|-----------|
| Typo / comment fix | ✅ Allowed | CI passes |
| CI config tweak | ✅ Allowed | Verified working |
| Minor docs | ✅ Allowed | No spec/content change |
| Source code / tests / features | ❌ **PR required** | Must pass CI + trace gates |

For this project, **prefer PR for everything** — it forces a review pass even as a single developer.

## CI Gates

GitHub Actions (`.github/workflows/ci.yml`) runs on every PR. See [03-pr-review-process.md](03-pr-review-process.md) for details.

## Release Process

See [04-release-process.md](04-release-process.md) for the full release procedure including versioning and CHANGELOG updates.

Quick tag command:

```bash
git tag -a v0.8.0 -m "v0.8.0 — description"
git push origin v0.8.0
```

## Related Documents

| Document | Description |
|----------|-------------|
| [02-commit-conventions.md](02-commit-conventions.md) | Conventional Commits format, one-change-one-commit rule |
| [03-pr-review-process.md](03-pr-review-process.md) | PR lifecycle, template, reviewer checklist, squash merge |
| [04-release-process.md](04-release-process.md) | Versioning, CHANGELOG, release checklist |
