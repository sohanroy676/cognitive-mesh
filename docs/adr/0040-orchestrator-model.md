# ADR-0040: Orchestrator model (the conductor)

## Status
Proposed (drafted in plan mode 2026-11-04; the four marked user decisions were taken before drafting). No code until the user accepts it.

## Date
2026-11-04

## Context
Extended roadmap v1.10.0 asks for a **conductor**: one model gets the request, the workspace graph, recalled
experience and decisions, and the model catalog with performance stats, and returns a plan with the roles needed,
the model for each, the order, and the reason for each choice. The plan is data, is validated before it runs, falls
back to another model when an assigned one fails, uses performance records only above a stated sample minimum, and
is itself a decision record (ADR-0039).

Facts from the code (read 2026-11-04):
- `PlanStep` and `TaskPlan` (`orchestrator/models.py`) are frozen with `extra="forbid"`; they have no role or model
  field. `plan_task` (`orchestrator/planner.py`) checks capabilities, references, input schemas and cycles.
- The orchestrator picks an agent by capability (`registry.find`, sorted by id). A fallback agent takes over after a
  failure result. A retryable provider error retries the same agent; no code switches models.
- A run builds **one** model client for all model steps (`cli.py::_registry_for`, `providers/factory.py::
  build_model_client`), and the catalog entry must list every role in the plan. `AGENT_ROLES` is
  `{code_explainer, dependency_analyst, planner}` (`providers/catalog.py`).
- `orchestrator/generate.py` already drafts a plan with a model: strict JSON parse, `plan_task`,
  `refuse_repeated_work`, `_refuse_unknown_files`, at most 2 drafts with `previous_rejection`. Drafting is not
  recorded anywhere.
- `model_calls` (trace schema 6 today, table added in 5) rows must belong to a plan step; they are written only in
  `TraceStore.finish_run`. `ModelStats` groups by (entry, provider, model, role, task type) and states that its
  numbers do not rank models.
- `record_decision` accepts `MadeBy.MODEL` (needs provider, model, role). An `agent_task_id` must match a recorded
  `model_calls` row. ADR-0039 says the conductor will write its decisions in the run's finishing transaction.
- `ProviderErrorKind`: `rate_limited`, `timeout` and `unavailable` are retryable; `auth`, `bad_response`, `other`
  are not. Highest exit code is 21. The workspace store has no "current project".

Constraints unchanged: memory is evidence, not truth; model-written text, including the conductor's reasoning, is
`model_unverified`; files, memories and model output are data, never instructions; no key, prompt or exception text
in any record or message; `COGMESH_ALLOW_REMOTE_CONTENT` and `COGMESH_ALLOW_TRAINING_USE` stay in force per entry;
default tests need no network.

## Decision

User decisions (2026-11-04):
1. **Sample minimum:** 20 finished calls with a known attempt outcome per (catalog entry, role, task type).
2. **Exploration:** off by default; an opt-in `--explore-rate`.
3. **Recording the conductor's call:** trace schema 7 allows run-level `model_calls` rows.
4. **Fallback order:** catalog file order, chosen by code, not named by the model.

Design:

1. **Command.** `task REQUEST --conduct --conductor ENTRY --catalog FILE --root APP ...`, mutually exclusive with
   `--plan`. `--workspace FILE --project NAME` is optional and names the current project; without a workspace the
   current project is `--root`. Recall, the gate and the context pack (`workflow.prepare_context`) are unchanged.
   `plan` (draft only, nothing runs) keeps its behaviour.
2. **Conductor input** (one JSON object, all of it data; sizes capped and the caps stated in the prompt module):
   the request; the capability list (as `generate._catalog`); the application's file list (capped as now); a
   workspace summary when given (projects, edge kinds and counts from `workspace/render.py` JSON, no file text);
   a summary of the context pack (ids, statuses and labels only); the catalog view (entry id, tier, `data_use`,
   remote host, roles, context size; never a key variable name or value); and per (entry, role, task type) the
   stats (calls, ok, attempt success and failure, unknowns) with the sample minimum and an `evidence` label that
   **code** computes.
3. **Conductor output.** Exactly `{"steps": [...], "assignments": {STEP_ID: {"model": ENTRY_ID, "reason": TEXT}},
   "reasoning": TEXT}`. The step objects are the existing `PlanStep` keys. A step whose capability uses a model must
   have an assignment; any other step must not. The **role is the step's agent id, derived by code**; the model
   never chooses a role string. `PlanStep` and `TaskPlan` are unchanged; a new `ConductorPlan` wraps a `TaskPlan`
   and its assignments. The role set is the existing agents plus `conductor` (added to `AGENT_ROLES`); reader,
   coder and tester arrive with v1.11.0 and v1.12.0.
4. **Validation, before anything runs.** The existing `plan_task`, `refuse_repeated_work` and
   `_refuse_unknown_files`, then:
   - every assigned entry exists in the catalog and lists the step's role;
   - every assigned entry is statically reachable: its key variable is set (the name is never printed), and the
     remote-content and training-use opt-ins pass for it. No network probe;
   - `CapabilitySpec` gains `writes: bool` (False for all current capabilities). A step whose capability writes
     must target the current project; any other project is read-only and such a plan is refused. Tested with a fake
     writing capability, since none exists yet;
   - reasons are non-empty, capped, and redacted like decision text.
   A refusal is returned to the conductor as `previous_rejection` (messages name ids only, never given text), with
   at most 2 drafts, as `generate.py` does. After the second refusal, or on a provider error, the run is refused
   with new exit code **22 `EXIT_CONDUCTOR_ERROR`**. A conductor plan never runs unvalidated, and no step runs
   if any step is invalid.
5. **Evidence label.** For each assignment, code counts calls in the trace database with the assigned entry, role
   and task type whose attempt outcome is known. At 20 or more the label is `recorded`; below, it is
   `insufficient_evidence`. The label and the counts are shown next to the assignment in the plan output and in
   `traces show`. Nothing is ever called better, best or proven; the numbers are the counts. The conductor's
   reason stays `model_unverified`; a `recorded` label does not make a choice correct, and the stats come from this
   one trace database only.
6. **Fallback.** A `RoutedClient` (`providers/routing.py`) sits where the single `RecordingClient` sits today. The
   orchestrator's `on_attempt` hook tells it which step is running, and it uses that step's assigned entry. On
   `rate_limited`, `timeout` or `unavailable` it tries the next entries in catalog file order that list the role
   and pass the static checks of point 4, at most 2 more. Each call is its own `model_calls` row with the entry that
   actually served it, and the switch (from entry, to entry, error kind) is recorded and printed. If every entry
   fails, the step fails with the last error kind; nothing is a silent success. `auth`, `bad_response` and `other`
   do not switch models. The existing same-agent retry for retryable errors is unchanged and happens first.
7. **Exploration.** `--explore-rate R` (0 to 0.2, default 0). With R > 0, a draw derived from the run id (a hash,
   so it is reproducible) may swap **at most one** model step to a different eligible entry. The swap, R and the
   original assignment are recorded in that step's decision. With R = 0 no step is ever swapped. The documentation
   says plainly that exploration may call a model the conductor did not choose.
8. **Trace schema 7.** `model_calls` may hold run-level rows: no step, `role = "conductor"`, `task_type = "conduct"`,
   `agent_task_id = {task_id}._conductor.{n}`. Every conductor draft call is recorded, including refused drafts
   (this also covers drafting for `--conduct`; `plan` stays unrecorded). `finish_run` gains a `decisions=`
   argument, and decisions are validated and written in the same transaction as the run, calls and steps, so a
   decision's `agent_task_id` finds its row. `ModelStats` shows the conductor role like any other. The
   migration is plain statements; a schema 6 database is upgraded by opening for writing.
9. **Decision records** (`MadeBy.MODEL`, `model_unverified`, provider, model and role of the conductor entry):
   - **One plan decision.** Options: each draft, with the reason a refused one was refused. Chosen: the accepted
     draft. Reasoning: the conductor's `reasoning`. It cites the recalled claims and decisions the conductor was
     offered, and the files of the application it was shown, and links to the conductor call.
   - **One decision per model step.** Summary written by code (role, task type, evidence label, counts, whether
     exploration swapped it). Options: the eligible entries, at most 10. Chosen: the assigned entry. Reasoning: the
     conductor's `reason` for that step. It cites the plan decision.
   - A fallback is recorded in the calls (entry per call), not rewritten into the decision. Run-level
     `run_id` is set on all decisions. A conductor decision never validates itself: its status comes from the
     ADR-0039 rules, and it reaches `validated` only through a recorded outcome.
10. **Not in v1.10.0:** a real reader, coder or tester role; a network reachability probe; cross-database stats;
    more than one conductor entry per run; a UI view of assignments; recording `plan` drafts; choosing the default
    conductor model (that is decided from records, not upfront).

## Alternatives considered
- **The conductor names the fallbacks:** the order would be model-written and unreviewed on exactly the path
  taken when something has already failed. Catalog order is deterministic and editable by the person.
- **Conductor decisions without a call link:** no schema change, but the conductor's own call would not be stored,
  so stats could not count it and a decision could not be traced to it.
- **Exploration on by default (10%):** collects comparison data automatically but calls models the person did not
  expect, with remote-content consequences.
- **Letting the model choose a role string:** roles are agents; a free-text role would need validation against the
  same table anyway and invites invented roles.
- **Adding `role`/`model` fields to `PlanStep`:** changes the schema every saved plan and test uses; a wrapper
  leaves hand-written `--plan` files valid.
- **A network probe for reachability:** needs a call per entry per run, costs quota on free tiers, and a probe
  success says nothing about the next call.

## Consequences
- Every conductor task records which roles ran, which model did each (and which actually served it), why, and the
  evidence label, all traceable with `decisions trace` and `traces show`.
- Model steps may use different models in one run; the single-client assumption goes away for `--conduct` only.
  Hand-written `--plan` runs behave as before.
- Trace schema 7 and one more write-open of old trace databases. The graph format and the index are unchanged.
- Routing is advisory evidence plus a code-computed label; with fewer than 20 samples the label says so, whatever
  the conductor wrote. A small sample is never described as proof.
- Limits stated up front: static reachability only; stats from one database; the sample minimum 20 is a
  judgement, not a measurement; exploration, when on, spends calls on unchosen models; the conductor's summary of
  the pack and workspace is only as good as its inputs.

## Tests
Fake conductor replies that are valid, cyclic, name an unknown entry, name an entry lacking the role, name an
entry whose key is unset or whose opt-in is missing, assign a write step to another project, are not JSON, have an
extra key, or give a deterministic step an assignment; a refusal followed by a valid second draft, and two
refusals ending in exit 22 with nothing run; one provider only; every entry rate-limited (explicit step failure,
all calls recorded, never a success); fallback order equals catalog order and stops after 2; `auth` does not
switch; stats with 0, 19 and 20 samples give `insufficient_evidence`, `insufficient_evidence`, `recorded`; calls
land under the right entry, role and task type and never mix task types; exploration is off at 0, deterministic
for a fixed run id, swaps at most one step, and is recorded; a plan decision and per-step decisions are written in
the run's transaction, join their `model_calls` row, and survive a graph rebuild; a model decision repeated five
times is never `validated`; secret text in a reason is redacted; schema 6 to 7 migration; run-level call rows
appear in `models stats`; persistence and `decisions list` in a new process; `--plan` runs unchanged; opt-in live
run with a free model (marker plus env var, skipped in CI).

## Fix if
A conductor plan runs without validation; a small sample is described as proof that one model is better; a key
appears in any prompt, record or message; a write step reaches another project; a missing metric is stored as zero;
a fallback happens without a recorded call; reasoning text is treated as evidence for itself.
