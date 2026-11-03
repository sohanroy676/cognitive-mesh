# Session prompt (maintained by the agent)

Update this file at the end of every session and whenever the user asks for a new-session prompt.

## Short prompt to paste into a new session
Continue Cognitive Mesh on branch `feature/extended-roadmap`. Read `session_prompt.md` at the repo root first and follow it. Update it at the end of the session or when I ask for a new prompt.

## Where we are (2026-11-03, session 6)
- Work: `docs/extended_roadmap.md` (v1.6.0 to v2.1.0, draft, nothing implemented). All changes go on `feature/extended-roadmap` (off main `3a4e4c8`, tag v1.5.0). Main is not touched. Merge to main only after the user approves the finished roadmap.
- Decided with the user: one long-lived branch; GitHub repos fetched with the `git` CLI as a subprocess; v1.6.0 supports public repos only (token vs `gh` login is decided in the v1.12.0 ADR).
- v1.6.0 is IMPLEMENTED on this branch (workspace package, CLI, tests; suite 1575 passed). Not done: HTML view of the workspace graph (ADR-0036 amendment); ask the user whether to add it or move on.
- Next: draft ADR-0037 (v1.7.0) after the user decides on the HTML view. Old note: user reviews ADR-0036 (`docs/adr/0036-workspace-and-cross-project-packages.md`); after acceptance implement v1.6.0 (plan: new `workspace/` package, tests, CLI, docs). Each later version gets its own ADR first.
- ADR status: ADR-0036 accepted. Next ADR: 0037 for v1.7.0 (API contracts, schemas, config), Proposed first, no code until accepted.

## Start of session
1. `git status`, `git branch -a`, `git tag --sort=-creatordate | head -3` (expect v1.5.0), `gh auth status` (must be sohanroy676), `gh run list --limit 6`.
2. Read this file, then `docs/extended_roadmap.md` for the current version, and `memory-bank/activeContext.md` / `progress.md` on demand.

## Rules
- Never commit to main. Work on `feature/extended-roadmap`. Never push, merge, tag or delete branches (local or remote) without approval. Local branches are all kept.
- Draft an ADR and get acceptance before any code that needs a design decision.
- Regenerate `CHANGELOG.md` as the last commit with plain `uv run git-cliff -o CHANGELOG.md` (`--tag vX.Y.Z` only on a `chore/release-vX.Y.Z` branch; compare the section date with `date -u +%F`). Only after the branch contains every commit. After any merge into main, run git-cliff to a temp file and diff against `CHANGELOG.md` (ignoring CR) BEFORE pushing.
- Never `git checkout` a file to undo an experiment while it has uncommitted work (use cp backups).
- Long commands run in the background with output to files (no `&` subshell inside the command). When timing, check for stray python processes first and compare in fresh processes, alternating.
- Files are CRLF in the working tree: edit with the Edit tool or Python (utf-8, newline=''); write multi-line scripts to the scratchpad with the Write tool.
- This checkout has a real `.env`: never read or print it. The venv is under a deny rule: run the CLI through `uv run --project`. Real-provider tests are opt-in.
- Document only commands actually run. If `.claude/settings.json` shows a change the user did not ask for, tell them and ask before reverting.
- End of session: update `memory-bank/progress.md` and `activeContext.md`; `docs/STATUS.md`, `docs/COMMANDS.md` and `README.md` when a version finishes; then this file.

## Parked open items (not started; the user chose the extended roadmap instead)
(f) v1.6.0 Model Performance Tracking and Routing (now folded into extended v1.8.0/v1.10.0); (d2) ADR-0023 amendment option 3 (re-measure recall cut-offs with 40+ independently written requests; 0.1 admitted off-topic claims for 9/20 unrelated queries, 0.15 for 3/20, 0.2 for 0/20); (b2) recall cost, about 13 s model load per process; (a) the two ADR-0030 decisions; (c) model-drafted plans using declared state; Dependabot PRs failing CI (types-networkx, pydantic-settings, sentence-transformers, ruff; runs from 2026-10-30, not investigated).

## Known gaps
No LICENSE file; `pyproject.toml` has `readme = "AGENTS.md"`; the five agents' code is gone (decisions in `data/agent-experiences/`); the UI was tried only on Windows with Chromium.
