# ADR-0011: Episodic trace store

## Status
Accepted (drafted and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.6.0 must persist what the orchestrator did so traces survive restarts and can be read in a later
session: task runs, steps, agent results, timestamps, model/provider identifiers, status, and some
environment information. Failed, blocked, incomplete and interrupted executions must be distinguishable
from successes, and a storage error must never leave a misleading record. Plan inputs and agent outputs
are untrusted and may contain secrets. Traces are raw experience, not validated knowledge: nothing here
may be read as "the system knows this is true" (promotion is a later version).

User decisions (2026-10-17): recording is opt-in through an explicit `--trace-db` flag; secrets are
redacted before storing; per-step timestamps come from an injected clock.

## Decision
- **Package** `cognitive_mesh.traces` (`models`, `store`, `recorder`). Standard-library `sqlite3` only; no
  new dependency. `sqlite3` is imported only in `store.py`, as NetworkX is private to `graph/graph.py`.
- **Ids.** `run_id` is `uuid4().hex` (factory injectable for tests). A step trace is `(run_id, step_id)`;
  an attempt is `(run_id, step_id, attempt_no)` and keeps the agent task id (`task.step.N`) and `agent_id`,
  so every stored output points back to its run, task, step and agent.
- **Schema** (`PRAGMA user_version = 1`, foreign keys on; an unknown version raises `TraceStoreError`):
  - `runs`: `run_id` PK, `task_id`, `state`, `status`, `started_at`, `finished_at`, `plan_json`,
    `source_root`, `source_fingerprint`, `env_json`, `redactions_json`.
  - `steps`: `run_id`, `seq`, `step_id`, `capability`, `status`, `agent_id`, `attempt_count`,
    `failure_kind`, `message`, `blocked_by_json`, `started_at`, `finished_at`; PK `(run_id, step_id)`.
  - `attempts`: `run_id`, `step_id`, `attempt_no`, `agent_task_id`, `agent_id`, `result_status`,
    `result_json`, `model_provider`, `model_name`, `basis`; PK `(run_id, step_id, attempt_no)`.
- **Success, failure, incomplete, interrupted.** The run header is committed with `state='running'`
  before the orchestrator starts. Steps, attempts and the final `status` are written in one transaction
  at the end. A header still `running` when read is reported as `interrupted` (a crash, or a storage
  failure that rolled back); it is never shown as succeeded. `status` is recomputed on read through
  `TaskRun.status`; a stored status that disagrees with the steps makes the record malformed, so a
  failed run cannot read back as succeeded.
- **Malformed records.** Rows are validated into frozen pydantic models on read. `get` on a bad row raises
  `TraceRecordError(run_id, reason)`; the reason names ids and pydantic error types, never values.
  `list` skips bad rows and reports their ids and a count. `sqlite3.Error` and `OSError` are wrapped in
  `TraceStoreError` whose message contains no SQL or values. Readers never create a database; a missing
  or non-SQLite file is an error.
- **Redaction.** `ingestion.secrets.redact_secrets` runs on every JSON string in plan inputs, attempt
  outputs and messages before insert. The rule names that matched (never values) go in `redactions_json`.
  Stored text may therefore differ from the live run; detection is pattern-based and not exhaustive.
- **Source and environment.** `source_root` is the resolved root; `source_fingerprint` is a sha256 over the
  sorted `(path, sha256)` pairs of the ingested files; `env_json` holds the cognitive-mesh version, the
  Python version and `platform.system()`. No hostname or user name.
- **Tool activity.** No tools exist beyond agents, so the attempts table is the activity log. Stated as a
  limit, not a feature.
- **Timestamps.** UTC ISO-8601. `Orchestrator` gets an optional `now: Callable[[], datetime] | None`.
  `StepRecord` gains optional `started_at`/`finished_at`, set only when `now` is given, so v0.5.0 output
  is unchanged without it.
- **CLI.** `run PLAN --root DIR --trace-db PATH` records the run and prints `trace: <run_id>`;
  `traces list --db PATH [--json]`; `traces show RUN_ID --db PATH [--json]`. Exit 8 for a trace store
  error or malformed record, exit 9 for an unknown run id. If recording fails after the run, the run result
  is still printed, a warning goes to stderr, and the exit code is 8. `*.sqlite3` is added to `.gitignore`.

## Alternatives considered
- **Always record to a default path** (env var). Writes files nobody asked for, and the setting would have
  to bypass `load_settings`, which requires `COGMESH_PROVIDER`. Not chosen.
- **Store verbatim.** Keeps exact text but writes any secret in an input or output to disk. Not chosen.
- **One JSON blob per run.** Simpler, but gives no per-step or per-attempt queries, and one bad field makes
  the whole run unreadable. Not chosen.
- **Write steps as they complete.** Gives better crash detail, but leaves partial step sets that look
  complete. The single final transaction plus the `running` header is easier to make honest. Revisit if
  long runs need it.
- **An ORM or a new dependency.** Not needed for three tables.

## Consequences
- Runs survive restarts and can be inspected by id, with their task, step, agent and model association.
- A crash is visible as `interrupted` rather than lost or misreported; no per-step detail is kept for an
  interrupted run.
- Redaction means a stored trace is not a byte-exact replay.
- No retention or size limits, a single writer assumed, no migrations beyond `user_version = 1`, and no
  per-attempt timing.
- Traces are raw records. Retrieval (v0.7.0) and validation before reuse (v1.1.0) are separate, later
  decisions.
