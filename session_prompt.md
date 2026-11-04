# Session prompt (maintained by the agent)

Update this file at the end of every session and whenever the user asks for a new-session prompt.

## Short prompt to paste into a new session
Continue Cognitive Mesh (extended roadmap, next v2.0.0; v1.13.0 is implemented, see 'Where we are'). Work only on branch `feature/extended-roadmap-v1.7` (do not branch off main); check it out and pull first. Read `session_prompt.md` at the repo root first and follow it. Do not use subagents (no Agent tool, no Explore/Plan agents); do all reading and searching yourself with Read, Grep and Glob. Update it at the end of the session or when I ask for a new prompt.

## Where we are (end of session 13, 2026-11-04)
- v1.13.0 (ADR-0043, accepted): `eval run TASKSET --out DIR --model ID --catalog FILE [--repeats N] [--allow-unisolated-checks]` and `eval report DIR [--eval-id N] [--json]`. A TOML task set (at most 20 tasks, each with files and its own check argv) runs through `change.pipeline.make_change` once per arm and repeat, memory off and memory on, one catalog entry; arm order alternates per task; each arm has its own trace db, index, graph, outcomes and worktrees under `DIR/r<R>/<arm>/`. The on arm gets earlier change drafts of its own arm only (`change/past.py`, `AgentContext.past_changes`, the coder prompt's `past_changes`; diff only for a draft whose check succeeded). Records: `DIR/eval.sqlite3` (append-only, `interrupted` derived). Report: counts, every item and pair, `mixed models` and unfinished pairs not counted, `too few paired tasks to conclude anything` below 20 pairs. Exit 25. No new dependency, no trace schema bump. Suite 2160 passed, 8 skipped (about 10 minutes).
- First thing next session: check `progress.md` for whether v1.13.0 was pushed, CI-tested and tagged `v1.13.0`; if not, finish that first (regenerate the changelog last, push the branch, `gh workflow run CI --ref feature/extended-roadmap-v1.7`, tag the tested commit, push the tag).
- Open from v1.13.0: no real model or free-tier service has been evaluated; only a 2-task stub run with the real MiniLM; `change propose` has no memory flags; the claims pack is empty in change-only evaluations; checks are not isolated; no UI view; no statistics by design.
- Next: v2.0.0 Local API server and chat (read `docs/extended_roadmap.md`): plan mode, draft the ADR as Proposed, no code until the user accepts. It replaces ADR-0031's read-only rule for that server only (actions, CSRF and token handling, model text shown as text only). Phase F (benchmarking at scale) starts only when the user says so.

## Where we were (end of session 11, 2026-11-04)
- v1.11.0 (ADR-0041, accepted): `workspace impact PROJECT --target T`, `workspace contract-diff PROJECT [--base REF]`, `dependency_analyst.dependents_of`, the `reader` role (`summarise_dependents`), read-only `WorkspaceAccess` on `AgentContext.workspace`, `--workspace FILE --project NAME` on `run`, `task --plan` and `task --conduct` (the `--root` folder must be that project's folder), exit 23. Verdicts `breaking|compatible|unknown|unused`; `unknown` is its own group and never a pass. `packaging` is a direct dependency. Suite 1984 passed, 8 skipped. Pushed and tagged `v1.11.0` on `a26875b`; CI: branch run 41808730767 (passed), tag run 41809189430 (passed). The first tag push was rejected by the remote (`failure`, no reason given) and worked unchanged on one retry.
- Open from v1.11.0: the reader has not run with a real model; no GitHub-cloned project used as a dependent by hand; submodule imports (`from pkg.sub import x`) are not followed; other local projects are read-only only through the accessor, not on disk; migration order is lexical file order; no UI view; no stored impact reports.
- Next: v1.12.0 Small-change agent (read `docs/extended_roadmap.md` v1.12.0): plan mode, draft ADR-0042 as Proposed, no code until the user accepts. It amends ADR-0005 (running the project's tests: which command, where, isolation, limits), needs the worktree flow, the size limit (proposal: 3 files, 100 changed lines), PR permissions and GitHub access (token in `.env` or the `gh` login). Reuse `impact.contract_diff`, `WorkspaceAccess.require_writable`, `CapabilitySpec.writes` and the conductor's `current_project`.

## Where we were (end of session 10, 2026-11-04)
- v1.10.0 (ADR-0040, accepted): `task --conduct --conductor ENTRY --catalog FILE [--explore-rate R] [--workspace FILE --project NAME]`. Conductor plan validated (plan_from_steps + assignment checks + `writes`), two drafts, exit 22 and nothing stored on failure; `RoutedClient` per-step models with catalog-order fallback (max 2); evidence label `recorded` at 20 known-outcome calls; trace schema 7 (run-level conductor calls, decisions written in `finish_run`); decisions may name a run without a step. Suite 1878 passed, 8 skipped. pushed and tagged `v1.10.0` on `2a47e97`; CI: branch run 41790077755 (passed), tag run 41790585121 (passed).
- Open from v1.10.0: no real model has conducted a task (`tests/test_live_conductor.py` unrun); conductor calls of a refused plan are lost; `models stats` credits the attempt outcome to every call in an attempt; decisions of conducted runs carry no observed git link; static reachability only; no UI view of assignments.
- Next: v1.11.0 Cross-project impact analysis (read `docs/extended_roadmap.md`): plan mode, draft ADR-0041 as Proposed, no code until the user accepts. Reuse `workspace` graph, read-only accessor, `CapabilitySpec.writes`, the conductor's `current_project`.

## Where we were (end of session 9, 2026-11-04)
- v1.9.0 (ADR-0039, accepted): decision records. Trace schema 6 (`decisions`, `decision_citations`, `decision_links`), graph format 7, `decisions record|link|list|show|trace` (exit 21), outcomes schema 3 (a decision id may be the subject), a `decisions` Chroma collection, `recall` returns decisions (`recall_decisions_for`, shared with the UI endpoint). Suite 1805 passed, 7 skipped. Pushed and tagged `v1.9.0` (`af1e252`); CI: branch run 41784754008 (passed), tag run 41785211643 (passed).
- Open from v1.9.0: no UI view of decisions; not in the `task` context pack; plan drafts not recorded; PR/branch links are never checked against GitHub; no model has written a decision yet (the v1.10.0 conductor will call `decisions.record.record_decision(..., made_by=MadeBy.MODEL)`, which needs a recorded `model_calls` row for `agent_task_id`).
- Next: v1.10.0 Orchestrator model (read `docs/extended_roadmap.md` v1.10.0): plan mode, draft ADR-0040 as Proposed on this branch, no code until the user accepts it. Every conductor plan is a decision record; routing may use `models stats` records only above a stated sample minimum.

## Where we were (end of session 8, 2026-11-04)
- Branch policy (user decision 2026-11-03): ALL extended-roadmap work (v1.7.0 onwards) happens on `feature/extended-roadmap-v1.7` and is pushed to `origin/feature/extended-roadmap-v1.7`. Do not branch off main or merge into main until the user approves completion of the whole extended roadmap. Each version: ADR first (Proposed, wait for acceptance), code, tests, docs, regenerate the changelog with `--tag vX.Y.Z` as the last commit, push the branch, wait for CI, then tag that commit and push the tag (the user pre-approved push and tag on this branch only). Merging to main, deleting branches and force-pushing still need explicit approval.
- v1.8.0 (ADR-0038, accepted): OpenAI-compatible client, TOML model catalog (no keys; https, key variable, `COGMESH_ALLOW_REMOTE_CONTENT`, and `COGMESH_ALLOW_TRAINING_USE` unless `data_use=no_training`), `--model/--catalog` on run/task/plan, token counts (unknown = None), call records in trace schema 5 (`model_calls`), `models list|stats`, exit 20. Suite 1704 passed, 7 skipped. Pushed and tagged `v1.8.0` (`f295b59`); CI: branch run 41770415795 and tag run 41770857133 passed (first branch run 41770047045 failed only the changelog date step after UTC midnight).
- Open from v1.8.0: no real remote service was ever called (Gemini/OpenRouter/Mistral request shapes unverified; `tests/test_live_openai_compat.py` not run, needs RUN_LIVE_OPENAI_COMPAT=1 plus URL, model and key); plan drafting is not recorded; 429 covers quota and rate limit alike; `data_use` is declared, not checked; catalog roles are agent ids (+ `planner`); no routing yet (v1.10.0).
- v1.7.0 (ADR-0037, accepted): API contract, shared schema and config edges across workspace projects, `workspace deps`. Suite 1604 passed, 6 skipped. Pushed and tagged `v1.7.0` (commit `752fa84`); CI: manual branch run 41683996261 and tag run 41684573022 passed.
- Open from v1.7.0: no HTML workspace view; Express routers mounted from another file stay `prefix_unknown`; destructured `process.env` and `from os import environ` are not seen; not run on Linux or on real GitHub repositories (CI covers Linux).
- Open from v1.6.0: no command moves a github pin, clone cache has no size limit, private repos only checked with a nonexistent repo.
- Next: v1.9.0 Decision records (read `docs/extended_roadmap.md` v1.9.0): plan mode, draft ADR-0039 as Proposed on this branch, no code until the user accepts. Decisions join the experience graph and the vector index (graph and index format bumps need care: ADR-0027 hash rules, ADR-0014 index key); model-written reasoning is `model_unverified`. Reuse `providers/recording.py` call records to link a decision to its model call.
- Decided earlier: GitHub fetch uses the `git` CLI; public repos only until the v1.12.0 ADR; other projects in a workspace are read-only.

## Workflow facts learned (session 13)
- A stub OpenAI-compatible server must send `finish_reason: "stop"` in the choice, or the client reports `bad_response`; the eval then exits 0 with every item `no_draft`, which is the report working as intended.
- The full suite now takes about 10 minutes; `test_eval_run.py` alone takes about 75 seconds (git worktrees plus Chroma). Run it in the background and wait for the `exit=` line.
- Source files in the working tree are LF (not CRLF) in this checkout; docs under `docs/` and `memory-bank/` may be CRLF, so edit them with a script that preserves line endings.

## Workflow facts learned (session 12)
- A Python edit script written inside a Bash heredoc turns `\\n` in its strings into a real newline in the target file (a test file got a broken string literal). For test text that needs a newline use `chr(10)` or edit with the Edit tool; check with `ruff` straight after.
- `ruff format` rewraps what an edit just wrote, so a second `edit()` that matches the old text can fail with `assert old in s`: re-read the lines, or edit the formatted form.
- A background command started with `( ... ) &` reports completion of the launcher at once; the real result is in the output file (wait for its `exit=` line, or the trailing summary). Remove the file first.
- `redact_secrets` takes time quadratic in an unbroken run (20,000 characters: 15 s). Never feed it a whole log; cut long lines first (`change/checks.py` does).
- Pinned versions that change with a schema bump now also include `tests/test_trace_conductor.py` (the 6 to 7 migration filter and the final version), `tests/test_decisions_record.py` (`SCHEMA_VERSION == 8` and the faked schema-5 test) and `tests/test_cli_decisions.py` (the schema-5 test drops the `change_events` and `changes` tables too).
- `traces/store.py::_migrate` re-reads the version under the write lock; a new migration must not rely on being run once, but the runner now guarantees it.
- The Windows `shutil.which` searches the current folder first; any command run in a folder a model can write must not be resolved there (`change/checks.py::_found_in_the_project`).

## Workflow facts learned (session 9)
- Long `python - <<'EOF'` or `cat <<'EOF'` blocks in one Bash call fail here ("unexpected EOF"); write patch scripts and file bodies with the Write tool into the scratchpad and run them. A small helper `edit(path, [(old, new)])` (read with newline='', normalise CRLF, assert the old text is present) lives in `%TEMP%/edit_helper.py`; recreate it if it is gone. Never put a real newline inside a Python string literal in a generated test (use `\n`); when a file looks unchanged after a script, check with `grep -n` before retrying.
- `uv run pytest` in a background command: if lint steps run first, the output file is not truncated yet, so a wait loop sees the previous run's `exit=` line; remove the file first or start pytest first.
- Windows clocks can repeat a microsecond; anything whose id hashes a timestamp must retry.
- Test pins that change on purpose with a format or schema bump: `tests/test_application_identity.py` (graph FORMAT_VERSION), `tests/test_graph_load_cost.py` (GOLDEN_HASH), `tests/test_trace_migrations.py` (key = SCHEMA_VERSION), `tests/test_valid_until_stores.py` (outcomes version).

## Workflow facts learned (session 8)
- The changelog section date is today's UTC date when generated with `--tag`; a CI run after UTC midnight fails the changelog step. Check `date -u` before regenerating and before CI; if CI crosses midnight, regenerate with `--tag vX.Y.Z`, commit, push, rerun CI, tag that commit.
- Pushes on this branch can be blocked by the auto-mode classifier even though session_prompt says they are pre-approved; when that happens ask the user to approve or to push with `! git push ...`. An explicit request in the user's message (as at the end of session 8) is enough.
- Big heredocs break in the Bash tool; write scripts with the Write tool or a short heredoc. The session scratchpad can vanish between turns; keep edit helper scripts small. Multi-line replacements on CRLF files must normalise line endings first (an `edit(path, [(old, new)])` helper that reads with newline='' and replaces after `
` to `
`).
- The full suite takes about 6 minutes; run it in the background and poll with a bounded loop (`sleep` alone is blocked).

## Workflow facts learned (session 7)
- CI triggers only on pushes to main, pull requests to main and `v*` tags. For a branch run use `gh workflow run CI --ref feature/extended-roadmap-v1.7` (full-history gitleaks scan; `.gitleaksignore` holds 3 exact fingerprints).
- GitHub push protection blocks secret-shaped literals (even fake ones). Build fake secrets at run time; do not name a string constant *TOKEN/*KEY/*SECRET (gitleaks flags it).
- Tag only the commit CI tested: regenerate the changelog with `--tag vX.Y.Z` as the last commit, push, run CI, then `git tag -a` that commit and push the tag.
- Large inline Python/heredoc scripts break in the Bash tool here; write scripts to the scratchpad with the Write tool and run them. Edit CRLF files with Python (newline='') or the Edit tool.

## Start of session
1. `git status`, `git branch -a`, `git tag --sort=-creatordate | head -3` (expect v1.12.0, or v1.11.0 if the tag is still to be made), `git checkout feature/extended-roadmap-v1.7 && git pull`, `gh auth status` (must be sohanroy676), `gh run list --limit 6`.
2. Read this file, then `docs/extended_roadmap.md` for the current version, and `memory-bank/activeContext.md` / `progress.md` on demand.

## Rules
- Do not use subagents (the Agent tool, including Explore and Plan agents). Do the exploration yourself with Read, Grep, Glob and Bash. User instruction, session 13.
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
