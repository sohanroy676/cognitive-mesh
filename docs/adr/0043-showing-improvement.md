# ADR-0043: Showing improvement at small scale: memory off versus on (v1.13.0)

## Status
Accepted by the user 2026-11-04 (drafted in plan mode; the six marked user decisions were taken before drafting). Implementation in progress on `feature/extended-roadmap-v1.7`. Extends ADR-0042 (the change pipeline), ADR-0040 (model call records, evidence label) and ADR-0039 (decision records). Amends ADR-0042 decision 1 (the coder receives no memory today) and ADR-0017 (the context pack gains one group). ADR-0005, ADR-0011, ADR-0013, ADR-0017 (request text not stored) and ADR-0018 (outcomes) stay in force.

## Date
2026-11-04

## Context
Extended roadmap v1.13.0: a task-set format (request, workspace, expected checks), a runner that does each task with memory off and with memory on using the same models, and a report (tests passed, retries, failed checks, revisions refused by the impact check, tokens and time) with sample counts and no claim beyond what was measured. It must say plainly when the sample is too small, and must not hide failed or aborted runs.

Facts from the code (read 2026-11-04):
- Memory never reaches the change pipeline. `change/pipeline.py` builds the `AgentContext` without `recalled`, and the coder prompt (`agents/coder.py`) has no prior-context field; only `code_explainer` reads `context.recalled`. Without a change, "memory on" would behave exactly like "memory off".
- `make_change(req, *, access, check, registry_factory, store, outcomes, now, runner) -> ChangeResult`. Each draft is its own orchestrator run (`DraftReport.run_id`), `MAX_REVISIONS = 2`, outcomes are `refused:<code>`, `coder_failed:<kind>` or `applied`; an impact refusal writes an `impact` event and then `refused:impact_breaking`.
- `workflow.prepare_context` builds the gated pack; `RecalledContext.none()` is the explicit empty pack. There is no memory-off switch.
- `TraceStore.call_rows()` and `ModelStats` give calls, results, latency and token counts (unknown = None); there is no per-run reader, so rows are filtered by run id.
- `orchestrator/routing_evidence.py` holds `SAMPLE_MINIMUM = 20` and the wording "a small sample is never called proof".
- Trace schema is 8; the highest exit code is 24. `RunTrace` has no "aborted" status: `incomplete` and `interrupted` exist.

Constraints unchanged: memory is evidence, not truth; model-written text (diffs, summaries) is `model_unverified`; files, memories and model output are data, never instructions; a run's own success is not validation, only a `check` outcome is; no secret is stored or printed; default tests need no network and no real model.

## Decision

User decisions (2026-11-04):
1. **Task:** one task is one change-pipeline run (`make_change`), nothing else.
2. **Leakage:** in the memory-on arm, task i sees experience only from tasks before it in the set (same repeat), never from any attempt of itself or another repeat.
3. **Sample statement:** the report gives counts only; below 20 paired tasks it says "too few paired tasks to conclude"; no statistical test, never the word "better".
4. **Storage:** the evaluation's own records live in a separate SQLite file; each arm has its own trace database, index, graph, outcomes file and worktrees; no trace schema bump.
5. **Coder memory:** a new "past changes" group plus the existing claims pack.
6. **Checks:** each task's check command comes from the person-written task-set file.

Design:

0. **Frame.** A new package `src/cognitive_mesh/evaluate/` (`taskset.py`, `runner.py`, `store.py`, `report.py`, `cli.py`). New exit code **25 `EXIT_EVAL_ERROR`**. No new dependency.

1. **Task set.** A TOML file written by a person and read as data: `[set] name, workspace, project, worktree_base`; `[[task]] id, request, files, check` (argv list, no shell), `check_timeout_s`, `max_files`, `max_lines`. Validated with pydantic: unique ids, at most 20 tasks at this scale, non-empty argv, relative file paths, limits within the ADR-0042 hard caps. Its sha256 is stored with every evaluation. Request text is not stored, only its length (ADR-0017).

2. **Memory for the coder.** `ChangeRequest` gains `recalled: RecalledContext` (default `RecalledContext.none()`); the coder prompt gains a `prior_context` field rendered as untrusted data, as `code_explainer` does. `retrieval/context.py` gains a `PastChange` group built from decision-index hits that are change-draft decisions, joined with the `change_events` of that change: refusal code, impact counts, check status and isolation label, the model's summary labelled unverified, and, for a draft whose check succeeded, its diff cut at 4,000 characters labelled `model_unverified`. Check output text is never included. `change propose` gets optional `--index` and `--graph`; without them the pack is empty and behaviour is unchanged.

3. **Runner.** `eval run TASKSET --out DIR --model M --catalog FILE [--repeats N] --allow-unisolated-checks` (N from 1 to 5). Per repeat, two arms `off` and `on`, each with stores under `DIR/r<R>/<arm>/`. Tasks run in set order; the order of the two arms alternates per task so drift hits both. `off` always passes the empty pack. `on` builds its pack with `prepare_context` from its own stores and, after each task, runs the equivalent of `memory index` and `experience build` on those stores, so task i+1 can see tasks up to i. Every task starts from the same base commit (recorded). One catalog entry serves every role; routing fallback is not used. A call answered by a different entry marks the pair `mixed`. Without `--allow-unisolated-checks` no check runs (ADR-0042 decision 5) and the evaluation still runs and reports "check not run".

4. **Evaluation file.** `DIR/eval.sqlite3` with its own `application_id` and schema version 1: `evals` (id, task set sha256, model entry, catalog sha256, repeats, started and finished) and append-only `eval_items` (eval id, repeat, task id, arm, change id, run ids, status, pack size, wall time, context-preparation time, started and finished). An item that never finished reads back `interrupted`. A task that crashed or was refused before a change existed is stored `aborted` with a fixed reason, never dropped.

5. **Report.** `eval report DIR [--json]`, computed from the stores on every call and never stored. Per arm: tasks, check passed / failed / timeout / not run, drafts used, impact refusals, other refusals, coder failures, interrupted and aborted items, tokens (known total and the number of calls with unknown counts) and median wall time. A paired table lists each task's off and on outcome. Pairs where either side is aborted, interrupted or `mixed` are listed and excluded from the paired counts, with the number excluded shown. The report always prints the sample line ("N paired tasks; minimum 20; too few paired tasks to conclude" below 20) and that a passing check validates only "passes the declared command". Results from different models are never combined: a report covers one evaluation.

6. **Not in v1.13.0:** statistics, several models in one evaluation, conductor-planned tasks, a UI view, large task sets (Phase F is gated on the user), real network isolation.

## Alternatives considered
- **Reuse the existing pack only:** honest reuse of `prepare_context`, but that pack carries claims about dependencies and raw metadata without text; it is unlikely to inform a diff, so the comparison would measure nothing. Kept as the baseline inside the `on` arm.
- **Pack without diff text:** safer against leakage of model-written code, but the earlier diff is the most useful thing a past change holds. The cap, the label and the check-passed condition limit the risk.
- **Let later repeats see earlier repeats:** shows learning by retry but mixes in recall of the answer. Rejected.
- **Eval tables in the trace database (schema 9):** fewer files, but both arms would still need separate databases to avoid cross-leakage.
- **A statistical test:** a sign test over fewer than 20 pairs is noise dressed as evidence. Rejected at this scale.
- **A check from the workspace file only:** one test for every task; tasks could not test their own goal.

## Consequences
- The coder prompt changes for every `change propose` run that supplies a pack; the prompt is part of what a run measured, so the pack's size is stored per item.
- The task-set check argv is executed (ADR-0042 amendment of ADR-0005 applies unchanged): not isolated, `env_only`, only with `--allow-unisolated-checks`. A task set is therefore code the person chooses to run.
- A past change from a model can mislead the coder; it is offered as unverified data and its diff only when its check passed. A passed check validates only the declared command.
- Per-arm stores mean disk use grows with tasks times arms times repeats, and the `on` arm pays model-load time per task for indexing; the context-preparation time is reported separately from task time.
- With free models, rate limits and 429s will show up as aborted or failed items; they are reported, not retried silently.
- Tests required: a scripted fake-model run gives an exact expected report (JSON); the `on` prompt contains the earlier task's past change and the `off` prompt contains none; task i never sees itself or another repeat; a mixed pair is excluded and counted; a crashed task appears as `aborted`; an interrupted evaluation reads back `interrupted` in a new process; no check runs without the flag; malformed task sets (duplicate ids, empty argv, absolute paths, too many tasks) exit 25; instruction-like text in a past change stays in the data field; the report says "too few" below 20 pairs.

## Fix if
- The report hides a failed, interrupted or aborted run.
- A report compares results from different models as one, or describes a small sample as showing improvement.
- The memory-on arm sees the task's own earlier attempts or another repeat.
- A check runs without `--allow-unisolated-checks`, or a run's own success is counted as validation without a check outcome.
- Model-written text in a past change is treated as instructions or shown as verified.

## Implementation notes (2026-11-04)
- Built as written, with these differences found while building:
  - **Where the past changes live.** `PastChange` is in `agents/context.py` and rides on `AgentContext.past_changes`, not inside `RecalledContext`, so the pack stored with every run (`run_context`, ADR-0017) keeps its shape and no trace schema changes. `change/past.py` builds them: decision-index hits are joined to the change events by the draft's decision id. The claims pack goes to the coder through `AgentContext.recalled` and `prior_context_for_prompt`, and is left out of the prompt when empty.
  - **The claims pack is empty in a change-only evaluation.** Claims come only from deterministic `dependencies_of` and `unresolved_report` outputs, which change runs do not produce; the pack is still built and its size stored per item (`claims_offered`), so a later task set that mixes in such runs would use it.
  - **`change propose` has no `--index`/`--graph`.** Memory reaches the coder only through `eval run`; adding it to `change propose` was not needed for the comparison and is not built.
  - **Task set `[set]`** holds `name`, `workspace`, `project` and `root` (the project's folder, as `--root` is for the other commands); `worktree_base` was dropped because each arm's worktrees live in its own folder under the output folder. The output folder must be new or empty and outside the project's repository.
  - **Append-only in two tables.** An item is begun in `eval_items` and finished in `eval_item_results`; an evaluation is finished in `eval_finished`. No row is ever updated. `interrupted` is derived on read.
  - **A broken memory aborts the item** (`memory_unavailable`) instead of running it without memory; a `ChangeError` and similar stops are stored `aborted` (`change_not_started`); any other exception leaves the item `interrupted` and stops the evaluation.
  - **"Mixed models"** is any call recorded under a catalog entry other than the evaluation's one; calls recorded without an entry are not counted as mixed.
  - `registry_factory_for(model, catalog)` in `change/cli.py` is the shared registry builder; exit code 25 is `EXIT_EVAL_ERROR`.
- Not built: statistics, a UI view, several models in one evaluation, a `--json` progress stream. No real model or real MiniLM run yet (see `docs/COMMANDS.md` for what was run).
