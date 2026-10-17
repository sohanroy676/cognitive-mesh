# /commit

Use this workflow to commit current changes on demand (outside a full feature/debug flow).

Steps:

1. Run `git status` and `git diff` to review changes.
2. Confirm tests pass (run the suite if unsure) — do not commit failing tests.
3. Run lint/format and fix warnings.
4. Self-review the diff per 04-code-quality-review.md.
5. Stage relevant files and write a Conventional Commits message per 02-commit-style.md.
   Ensure the summary line makes sense standalone to someone reading only the changelog later,
   not just someone reading the diff.
6. Commit. Do not push without explicit approval.
7. Regenerate CHANGELOG.md with `uv run git-cliff -o CHANGELOG.md` (git-cliff) — do not hand-write changelog entries —
   and commit it as `chore(changelog): regenerate`. Do this last: any later commit that is not a
   merge or `chore(changelog)` makes the file stale and CI fails. At release closeout, use
   `--tag vX.Y.Z` before the tag exists.
