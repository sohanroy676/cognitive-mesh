---
description: Implement a feature end-to-end (criteria → branch → code → tests → commit → memory bank)
---

Implement this feature: $ARGUMENTS

Steps:

1. Restate acceptance criteria (inputs/outputs, edge cases, what "done" looks like) before writing code. If the request is vague, ask for the missing specifics first.
2. Check `memory-bank/architecture.md` and `memory-bank/systemPatterns.md` for conventions, and `ROADMAP.md` / `docs/DETAILED_ROADMAP.md` to confirm the feature's priority and verification criteria match the plan.
3. Create a feature branch (`.claude/rules/05-git-workflow.md`).
4. Implement with minimal, targeted changes.
5. Write/update tests per `.claude/rules/03-testing.md` and run the full suite.
6. Run lint/format and fix all warnings.
7. Self-review per `.claude/rules/04-code-quality-review.md`.
8. Commit per `.claude/rules/02-commit-style.md`.
9. Update `memory-bank/progress.md` and `activeContext.md`, and update `ROADMAP.md` to move the feature out of "Now"/"Next" only if its verification criteria actually passed. After the last commit, regenerate `CHANGELOG.md` with `uv run git-cliff -o CHANGELOG.md` and commit it as `chore(changelog): regenerate` (see `/commit` step 7).
10. Summarize the change in plain English and note it is ready for review/merge. Do not push or merge without approval.
