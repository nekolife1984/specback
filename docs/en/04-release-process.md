# Release Process

## Versioning

This project follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`):

| Bump | When | Example |
|------|------|---------|
| **MAJOR** | Breaking changes | `v1.2.0` → `v2.0.0` |
| **MINOR** | New features, non-breaking enhancements | `v1.1.0` → `v1.2.0` |
| **PATCH** | Bug fixes, hotfixes | `v1.2.0` → `v1.2.1` |

Pre-release suffixes: `v1.2.0-alpha.1`, `v1.2.0-beta.1`

## Step-by-Step

### 1. Prepare CHANGELOG

Ensure `CHANGELOG.md` is up to date with all changes since the last release. The format follows [Keep a Changelog](https://keepachangelog.com/):

```markdown
## [v1.2.0] - 2026-08-01

### Added
- feat: specback-search MCP server (#270)
- feat: template catalog validation (#299)

### Changed
- chore: upgrade pytest to 9.x
```

### 2. Update README

Bump the version references in [`README.md`](../README.md): the `## Status` section and the version mention in the "Why specback?" / "なぜ specback なのか？" bullet. (The old "README Roadmap" step was removed — README has no Roadmap section anymore.)

### 3. Tag and Push

```bash
# From main, after PR merge
git tag -a v1.2.0 -m "v1.2.0"
git push origin v1.2.0
```

### 4. Create GitHub Release

```bash
gh release create v1.2.0 \
  --title "v1.2.0" \
  --notes "See CHANGELOG.md for details"
```

Or create it manually at https://github.com/nekolife1984/specback/releases/new

## Hotfix Release

For urgent fixes on a released version:

```bash
git checkout -b fix/hotfix-crash main
# fix → commit → PR → squash merge → main
git tag -a v1.2.1 -m "v1.2.1 — crash fix"
git push origin v1.2.1
```

## Release Cadence

There is no fixed schedule. Releases are made when:

- A meaningful feature milestone is reached
- A critical bug is fixed
- A breaking change needs coordination

As of v1.2.0 the project is in the stable v1.x series. Breaking changes require a MAJOR version bump (see Versioning above).

## Release Checklist

- [ ] CHANGELOG.md updated
- [ ] README version references updated
- [ ] Version string in `skills/specback/SKILL.md` updated (if applicable)
- [ ] Tag created and pushed
- [ ] GitHub Release created
