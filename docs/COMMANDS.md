# Commands and exit codes

The full command list. Every command below was run successfully (the verified list with dates and outputs is in [`memory-bank/tech-stack.md`](../memory-bank/tech-stack.md)). For a first walk-through see the [README](../README.md).

## Setup and usage

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (Windows: `winget install --id=astral-sh.uv -e`).

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run git-cliff -o CHANGELOG.md   # regenerate CHANGELOG.md from Conventional Commits; CI fails if stale
```

Commands below were run successfully (see `memory-bank/tech-stack.md` for the verified list):

```
COGMESH_PROVIDER=ollama uv run cognitive-mesh           # validates configuration, exit 0
uv run cognitive-mesh ingest PATH [--json] [--include-content]
uv run cognitive-mesh graph PATH [--json] [--deps-of NODE_ID [--transitive]]
uv run cognitive-mesh graph PATH --format mermaid|html [--output FILE] [--max-nodes N] [--max-edges N]
uv run cognitive-mesh agents [--json]                  # lists roles and capabilities, no config needed
uv run cognitive-mesh run PLAN.json --root DIR [--json]   # runs a task plan, no config needed
uv run cognitive-mesh run PLAN.json --root DIR --trace-db FILE   # also records the run (SQLite)
uv run cognitive-mesh traces list --db FILE [--json]
uv run cognitive-mesh traces show RUN_ID --db FILE [--json]
uv run cognitive-mesh memory index --db FILE --index DIR [--json]   # needs no provider; first use downloads the model
uv run cognitive-mesh memory search "QUERY" --db FILE --index DIR [--k N] [--min-similarity X] [--json]
uv run cognitive-mesh experience build --db FILE --graph FILE [--json]   # needs no provider and does not load the embedding model
uv run cognitive-mesh experience claims --db FILE --graph FILE [--include-unverified] [--json]
uv run cognitive-mesh experience show NODE_ID --db FILE --graph FILE [--transitive] [--incoming] [--include-unverified] [--json]
uv run cognitive-mesh experience check --db FILE --graph FILE [--json]
uv run cognitive-mesh experience claims --db FILE --graph FILE [--reviews FILE] [--root DIR] [--outcomes FILE] [--json]   # adds evidence status and recorded outcomes
uv run cognitive-mesh experience review CLAIM_ID --verdict confirmed|rejected --note TEXT --by NAME --root DIR --graph FILE --reviews FILE [--json]
uv run cognitive-mesh experience outcome CLAIM_ID --source check|person --result succeeded|failed|ambiguous --detail TEXT --by NAME --root DIR --graph FILE --outcomes FILE [--json]
uv run cognitive-mesh experience promote CLAIM_ID --by NAME --root DIR --db FILE --graph FILE --outcomes FILE [--reviews FILE] [--json]   # exit 17 if refused; the refusal is recorded
uv run cognitive-mesh recall "QUERY" --db FILE --index DIR --graph FILE [--reviews FILE] [--root DIR] [--k N] [--min-similarity X] [--json]   # needs no provider
uv run cognitive-mesh task "REQUEST" --plan PLAN.json --root DIR --db FILE --index DIR --graph FILE [--reviews FILE] [--k N] [--min-similarity X] [--json]   # recalled context + run; no provider unless the plan has a model step
uv run cognitive-mesh workspace add PATH|https://github.com/OWNER/REPO --workspace FILE [--name NAME] [--cache DIR]   # v1.6.0, ADR-0036: public GitHub only; pins the commit it cloned
uv run cognitive-mesh workspace list|refresh|graph --workspace FILE [--json] [--format mermaid] [--cache DIR]   # read commands never create the file
uv run cognitive-mesh workspace deps PROJECT --workspace FILE [--json] [--cache DIR]   # v1.7.0, ADR-0037, run 2026-11-03 in a scratch directory (3 local projects: FastAPI, TS client, SQL): exit 0, sentences with evidence for API, schema, config and package edges and the project's unresolved items; unknown project: exit 19 "no project has that name"; missing workspace file: exit 19, nothing created
uv run cognitive-mesh workspace graph --workspace FILE   # v1.7.0: also lists API contract, shared schema and config edges (specific and generic names apart) and counts of unresolved items by kind and reason; --json has the full unresolved list; --format mermaid draws the new kinds
uv run cognitive-mesh workspace remove NAME --workspace FILE   # hides the project and its edges, keeps its rows
uv run cognitive-mesh workspace link SOURCE TARGET --kind K --note TEXT --by NAME --workspace FILE   # a declared edge, never detected evidence
uv run cognitive-mesh workspace impact PROJECT --target T --workspace FILE [--json] [--cache DIR]   # v1.11.0, ADR-0041, run 2026-11-04 in a scratch directory (a git workspace of two projects: FastAPI + SQL + env example + a package, and a Python client; script in the session scratchpad, not in the repo): exit 0, `route:GET /users` lists web with provider and consumer sites; `--target package` lists the package and the names imported; a target the project does not have: exit 23 "the target was not found in the current project"; an unknown project: exit 19
uv run cognitive-mesh workspace contract-diff PROJECT [--base REF] --workspace FILE [--json] [--cache DIR]   # v1.11.0, same scratch workspace after editing the working tree (a route renamed, a column length changed, an env example line removed, a version bumped past a declared range, an export dropped): exit 0, 4 breaking, 1 unknown, 1 compatible, each with reason code and sites; `--base nope`: exit 23; a project outside git: exit 23; nothing in the repository changed (status, refs and worktrees compared in a test)
uv run cognitive-mesh workspace set-check --workspace FILE --by NAME PROJECT -- COMMAND ARG...   # v1.12.0, ADR-0042, run 2026-11-04 in a scratch directory (a one-file git project `calc`, a scratch stub OpenAI-compatible server on 127.0.0.1:18790, not part of the repo): exit 0, "declared check #1 for calc (timeout 300 s): python -c import calc"; the words are stored as an argv list, never run through a shell (no words: exit 19, tests only)
uv run cognitive-mesh workspace show-check PROJECT --workspace FILE [--json]   # same run: exit 0, prints the command, its timeout and who declared it
uv run cognitive-mesh change propose "REQUEST" --root DIR --workspace FILE --project NAME --db FILE --outcomes FILE --worktrees DIR [--file PATH ...] [--base REF] [--max-files N] [--max-lines N] [--model ID --catalog FILE] [--allow-unisolated-checks] [--json]   # same run, catalog entry `stub` with role `coder`: without the flag exit 0, "unchecked", the note that the check will not run, nothing stored as an outcome; with it exit 0, "check_passed", one `check` outcome `succeeded ... isolation env_only` on the draft's decision; the project's checked-out branch and working tree stayed untouched, a branch `cogmesh/change-ID` held the commit in `wts/`; exit 7 when no draft applied or the check failed, exit 24 for a bad limit or a worktrees folder inside the repository (both from the tests, not by hand)
uv run cognitive-mesh change list --db FILE [--json]; uv run cognitive-mesh change show ID --db FILE [--json]   # same run: exit 0; show prints the events (impact counts, applied commit with the draft decision id, check with the outcome number); unknown id: exit 24; missing database: exit 8
uv run cognitive-mesh change approve ID --db FILE --root DIR --by NAME [--note TEXT]   # same run: exit 0, a person's decision citing the draft decision; exit 24 when the branch no longer points at the recorded commit
COGMESH_ALLOW_REMOTE_CONTENT=true uv run cognitive-mesh change pr ID --db FILE --root DIR [--base BRANCH] [--without-passing-check]   # same run, only the refusals: exit 24 "the origin remote is not a github.com repository"; after adding an origin, exit 24 "GITHUB_TOKEN is not set" with a `push_failed not_configured` event and nothing pushed. The push and the pull request themselves were not run against github.com (tests use a local bare repository and a local stub server)
uv run cognitive-mesh change discard ID --db FILE --root DIR   # exit 0 in the tests (`tests/test_cli_change.py`): removes the worktree, keeps the branch; not run by hand
uv run cognitive-mesh run PLAN --root DIR --workspace FILE --project NAME   # v1.11.0, same scratch workspace, plan with a `dependents_of` step: exit 0; without `--workspace` the step fails `missing_context` (exit 7); a `--root` that is not the project's folder: exit 19
uv run cognitive-mesh run PLAN --root DIR --model ID --catalog FILE [--trace-db FILE]   # v1.8.0, ADR-0038, run 2026-11-04 in a scratch directory against a scratch stub OpenAI-compatible server on 127.0.0.1 (not part of the repo): exit 0, explain_file succeeded, call recorded; a remote entry without COGMESH_ALLOW_REMOTE_CONTENT exits 2 naming it, then without COGMESH_ALLOW_TRAINING_USE exits 2 naming that; with both and a placeholder key the stderr warning names host, tier and data_use, and the call to a non-resolving host fails as provider_unavailable (exit 7), recorded; also `task` and `plan` take --model/--catalog (tests only)
uv run cognitive-mesh task "REQUEST" --conduct --conductor ENTRY --catalog FILE --root DIR --db FILE --index DIR --graph FILE [--explore-rate R] [--workspace FILE --project NAME] [--reviews FILE] [--outcomes FILE] [--json]   # v1.10.0, ADR-0040, run 2026-11-04 in a scratch directory through the real CLI and HTTP transport against a scratch stub OpenAI-compatible server on 127.0.0.1 (script in the session scratchpad, not in the repo; sample_app; no real model): exit 0; the conductor (entry `cond`) assigned `alpha`, which answered 429; `beta` took over ("model switch in conducted.explain.1: alpha -> beta after rate_limited"); the plan, reasons, evidence label (`insufficient_evidence: 0 call(s)...`) and `exploration: off` were printed; with `--explore-rate 0.2 --json` exit 0 and a `conductor` object; `--conductor` naming an entry without the role `conductor`: exit 2; `--conduct` with `--plan`: exit 2. Exit 22 (no usable plan, nothing run) is covered by tests, not run by hand. Not run: a real model, `--workspace`/`--project`, `--explore-rate` with a swap drawn
uv run cognitive-mesh traces show RUN_ID --db FILE   # v1.10.0: for a conducted run also prints the conductor decisions (plan and per step, model_unverified), the model calls per attempt and any model switch; same scratch run: exit 0
uv run cognitive-mesh models list [--catalog FILE] [--json]   # v1.8.0: no network call; key variable names and whether each is set, never a value; same scratch run: exit 0; missing catalog file: exit 20
uv run cognitive-mesh models stats --db FILE [--task-type T] [--json]   # v1.8.0: counts per entry/provider/model/role/task type with sample sizes, latency median and p90 of ok calls, token totals with unknown counts; same scratch run: exit 0; missing database: exit 8, nothing created
uv run cognitive-mesh decisions record --db FILE --by NAME --summary T --option T [--option T] --chosen T --reasoning T [--cite-node ID] [--cite-file PATH] [--cite-decision ID] [--supersedes ID] [--run ID --step ID] [--root DIR] [--app-id ID] [--json]   # v1.9.0, ADR-0039, run 2026-11-04 in a scratch directory (dep_app copy, real MiniLM): exit 0, prints the id, application and code version; `--root` that is not a directory: exit 3; citing a file without `--root` or a file the root does not have: exit 21; creates or upgrades the trace database to schema 6; `--from-json FILE` instead of the content flags
uv run cognitive-mesh decisions link ID --db FILE --by NAME (--commit SHA | --branch NAME | --pr REF)   # v1.9.0: exit 0, stored as `declared` and never checked; malformed value or unknown id: exit 21
uv run cognitive-mesh decisions list|show ID|trace (ID | --commit SHA | --branch NAME | --pr REF) --db FILE [--graph FILE] [--reviews FILE] [--outcomes FILE] [--root DIR] [--json]   # v1.9.0: read-only, same scratch run: exit 0; without `--root` cited files and outcomes cannot be matched to the code and the output says so; edited cited file: `stale`; with `--outcomes` and a succeeded outcome: `validated`; unknown id or reference: exit 13; missing database: exit 8, nothing created; unreadable row: exit 21 for `show`
uv run cognitive-mesh experience outcome DECISION_ID --source S --result R --detail T --by NAME --root DIR --graph FILE --outcomes FILE   # v1.9.0: accepts a decision id as well as a claim id (the decision must be in the graph); same scratch run: exit 0
uv run cognitive-mesh memory index --db FILE --index DIR   # v1.9.0: also indexes decisions (`decisions: N added, ...`); `--json` gains a `decisions` object
uv run cognitive-mesh recall QUERY --db FILE --index DIR --graph FILE [--root DIR]   # v1.9.0: adds a `decisions` group (found by meaning, or because it cites a recalled claim or attempt); an index built before v1.9.0 says it has no decisions collection
uv run cognitive-mesh ui [--db FILE] [--graph FILE] [--index DIR] [--reviews FILE] [--outcomes FILE] [--root DIR] [--port N] [--no-browser]   # local read-only web UI on 127.0.0.1; prints a URL with this session's token (ADR-0031)
```

Exit codes: 0 ok, 2 configuration or usage error, 3 unusable ingestion root, 4 unknown graph node,
5 output file could not be written, 6 invalid or unreadable plan, 7 task failed or incomplete, 8 trace store error or malformed trace, 9 unknown trace run id, 11 memory index missing, unusable or built with another model, 12 experience graph file missing or unusable, 13 unknown experience graph node, 14 graph sources not confirmed (`experience check`), 15 reviews file missing or unusable, or a review that was refused, 14 `experience check` found trace sources that no longer match, 18 `ui` could not listen on its port, 19 `workspace` file, project or fetch problem, 20 `models list` found no usable catalog (a bad catalog for `run`, `task` or `plan` is exit 2), 21 `decisions` invalid record or unusable decisions table, 22 `task --conduct` got no usable plan from the conductor (nothing ran), 23 `workspace impact` or `contract-diff` could not use the target, ref or project, 24 `change` could not make, keep or publish a change (a refused or failed publish names the reason; the branch stays).
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. `uv build --out-dir DIR` builds an sdist and wheel (wheel installed and run in a fresh venv on Windows; not published, not run in CI). The package version is derived from git tags (hatch-vcs): a build at tag `vX.Y.Z` is `X.Y.Z`, later commits are `X.Y.(Z+1).devN`, and a checkout without tags is `0.1.devN`.
