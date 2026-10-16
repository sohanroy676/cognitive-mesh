---
description: Fix a bug with reproduce → root cause → minimal fix → regression test
---

Fix this bug or failing behavior: $ARGUMENTS

Steps:

1. Reproduce the issue first — run the app/tests and confirm you can see the failure before changing anything.
2. Isolate the root cause (don't guess-patch symptoms). Trace back through logs, stack traces, and relevant code.
3. State the root cause in one or two sentences before writing the fix.
4. Implement the minimal fix — do not refactor unrelated code.
5. Write a regression test that would have caught this bug, plus any other needed coverage (`.claude/rules/03-testing.md`).
6. Run the full test suite to confirm the fix works and nothing else broke.
7. Self-review per `.claude/rules/04-code-quality-review.md`.
8. Commit per `.claude/rules/02-commit-style.md`, using the `fix:` type and referencing the root cause in the body.
9. Update `memory-bank/progress.md` with what was fixed.
