# PR Review Process

## Overview

All source code, test, and feature changes must go through a Pull Request. This ensures every change is reviewed, CI passes, and documentation stays in sync.

## PR Lifecycle

```
1. Branch from main → 2. Commit changes → 3. Push → 4. Open PR
                                                      ↓
                                              CI runs automatically
                                                      ↓
                                          5. Review (self or peer)
                                                      ↓
                                          6. Squash merge → main
                                                      ↓
                                          7. Delete branch
```

## Filling the PR Template

The repository has a `.github/pull_request_template.md` that appears when you open a PR. Here's how to fill each section:

### Summary
State what the change does and **why**. A good summary answers: "What problem does this solve?"

### Change Type
Check all that apply:
- `feat:` — New feature or enhancement
- `fix:` — Bug fix
- `chore:` — CI, refactoring, maintenance, dependencies
- `docs:` — Documentation only
### Checklist
Walk through every item before marking it done:

| Item | Why |
|------|-----|
| Branch name follows convention | Keeps history navigable |
| One branch = one logical change | Clean squash merge |
| `pytest tests/ -q` passes | CI gate — must be green |
| `pyrefly check scripts/` passes (if applicable) | CI gate — blocking |
| Trace/drift gate passes (if applicable) | Spec integrity |
| `CHANGELOG.md` updated | Release readiness |
| Docs synced (EN + JA) | Bilingual consistency |

### Related Issue
- `Closes #N` to auto-close the issue on merge
- `refs #N` to reference without closing

## Reviewer Checklist

Whether reviewing your own PR or someone else's, check:

### Function
- [ ] Does the change do what it claims?
- [ ] Are there edge cases not handled?
- [ ] Does it break existing behavior?

### Tests
- [ ] New scripts have a corresponding test file?
- [ ] Existing tests still pass?
- [ ] Are test assertions meaningful?

### Documentation
- [ ] EN docs updated?
- [ ] JA docs updated?
- [ ] README affected? Updated?
- [ ] AGENTS.md / CONTRIBUTING.md affected? Updated?
- [ ] CHANGELOG.md updated?

### i18n Consistency
- [ ] EN and JA versions say the same thing (not a translation mismatch)?
- [ ] Technical terms (branch names, CLI flags, file paths) match between languages?

## Squash Merge

This project uses **squash merge** exclusively. The PR title becomes the commit message on `main`, so:

> **Make your PR title a good conventional commit message.**
> Example: `feat: add Flask extraction guide (#42)`

After squash merge, **delete the branch** both remotely and locally:

```bash
git checkout main
git branch -D feat/your-branch        # local
git push origin --delete feat/your-branch  # remote
```

## What If CI Fails?

1. Check which step failed (click "Details" on the CI check)
2. Fix the issue locally
3. Commit and push — CI re-runs automatically
4. Repeat until all green

CI is configured with `cancel-in-progress`, so pushing a new commit cancels the previous run automatically.

## Bypass (Emergency Only)

```bash
git commit --no-verify              # Skips pre-commit hook
git push --no-verify origin main    # Skips pre-push hook
```

Use only for genuine emergencies (broken main, urgent hotfix). Follow up with a proper PR afterward.
