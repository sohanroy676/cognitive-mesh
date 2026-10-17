---
description: Review, test, and commit current changes per project rules (never pushes)
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git add:*), Bash(git commit:*), Bash(git log:*), Bash(git branch:*)
---

Commit the current changes on demand (outside a full feature/debug flow). Extra instructions from the user: $ARGUMENTS

Steps:

1. Run `git status` and `git diff` to review changes. If on `main`/`master`, create a feature branch first (see `.claude/rules/05-git-workflow.md`).
2. Confirm tests pass (run the suite if unsure) — do not commit failing tests. If the test command is still TBD in `memory-bank/tech-stack.md`, say so instead of inventing one.
3. Run lint/format and fix warnings (same caveat if tooling is not yet configured).
4. Self-review the diff per `.claude/rules/04-code-quality-review.md`.
5. Stage relevant files by name and write a Conventional Commits message per `.claude/rules/02-commit-style.md`. The summary line must make sense standalone to someone reading only the changelog later.
6. Commit. Do not push without explicit approval.
7. Regenerate `CHANGELOG.md` with `uv run git-cliff -o CHANGELOG.md` (git-cliff; never hand-write entries) and commit it as `chore(changelog): regenerate`. Do this last: any later commit that is not a merge or `chore(changelog)` makes the file stale and CI fails. At release closeout, use `--tag vX.Y.Z` before the tag exists.
