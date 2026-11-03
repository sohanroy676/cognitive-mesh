# Session prompt (maintained by the agent)

Update this file at the end of every session and whenever the user asks for a new-session prompt.

## Short prompt to paste into a new session
Continue Cognitive Mesh (extended roadmap, next v1.8.0). Work only on branch `feature/extended-roadmap-v1.7` (do not branch off main); check it out and pull first. Read `session_prompt.md` at the repo root first and follow it. Update it at the end of the session or when I ask for a new prompt.

## Where we are (end of session 7, 2026-11-03)
- Branch policy (user decision 2026-11-03): ALL extended-roadmap work (v1.7.0 onwards) happens on `feature/extended-roadmap-v1.7` and is pushed to `origin/feature/extended-roadmap-v1.7`. Do not branch off main or merge into main until the user approves completion of the whole extended roadmap. Each version: ADR first (Proposed, wait for acceptance), code, tests, docs, regenerate the changelog with `--tag vX.Y.Z` as the last commit, push the branch, wait for CI, then tag that commit and push the tag (the user pre-approved push and tag on this branch only). Merging to main, deleting branches and force-pushing still need explicit approval.
- v1.7.0 (ADR-0037, accepted): API contract, shared schema and config edges across workspace projects, `workspace deps`. Suite 1604 passed, 6 skipped. See `memory-bank/progress.md` for the CI run ids and the tag.
- Open from v1.7.0: no HTML workspace view; Express routers mounted from another file stay `prefix_unknown`; destructured `process.env` and `from os import environ` are not seen; not run on Linux or on real GitHub repositories (CI covers Linux).
- Open from v1.6.0: no command moves a github pin, clone cache has no size limit, private repos only checked with a nonexistent repo.
- Next: v1.8.0 (any model with an API key, model performance records; read `docs/extended_roadmap.md` and `docs/DETAILED_ROADMAP.md`): plan mode, draft ADR-0038 as Proposed on this branch, no code until the user accepts. ADR-0013 (`COGMESH_ALLOW_REMOTE_CONTENT`) must stay in force.
- Decided earlier: GitHub fetch uses the `git` CLI; public repos only until the v1.12.0 ADR; other projects in a workspace are read-only.

## Start of session
1. `git status`, `git branch -a`, `git tag --sort=-creatordate | head -3` (expect v1.7.0), `gh auth status` (must be sohanroy676), `gh run list --limit 6`.
2. Read this file, then `docs/extended_roadmap.md` for the current version, and `memory-bank/activeContext.md` / `progress.md` on demand.

## Rules
- Never commit to main. Extended-roadmap work stays on `feature/extended-roadmap-v1.7`. Never push, merge, tag or delete branches (local or remote) without approval. Local branches are all kept.
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
