# ADR-0010: Basic orchestrator and task planning

## Status
Accepted (design approved by the user 2026-10-17: run budget without a watchdog, ordering-only dependencies, `run` CLI command)

## Date
2026-10-17

## Context
v0.5.0 needs a task and step model, planning with dependencies, assignment of steps to agents by
declared capability, coordinated execution, and defined behavior for failed, blocked and incomplete
work. ADR-0009 gave agents a `run` that never raises and results that are `AgentSuccess | AgentFailure`
with `retryable`. Per-call timeouts are provider-owned. The orchestrator must not need to know any
agent's internals, must never report a failed task as successful, and must not loop without a bound.
Plans, step inputs and agent outputs are untrusted data.

## Decision
- **Package** `cognitive_mesh.orchestrator` (`models`, `planner`, `runner`). No new dependency.
- **Model.** `TaskPlan(task_id, steps)`; `PlanStep(step_id, capability, inputs, depends_on, max_attempts)`.
  Frozen pydantic models, `extra="forbid"`; plans loaded from JSON are validated with `strict=True`.
  Ids are pattern-restricted so the derived agent task id `{task}.{step}.{attempt}` is safe to echo.
- **Validation before anything runs** (`plan_task`, raises `PlanError`, the only exception raised):
  1 to 100 steps, unique ids, no self-dependency, every dependency exists (a missing dependency means
  nothing runs), no cycles, every capability has at least one registered agent. Messages name step
  ids and capability names only, never input values.
- **Ordering.** Deterministic topological order (declaration order breaks ties). Sequential execution.
- **Assignment.** `registry.find(capability)[0]` (sorted by agent id). No fallback to another agent.
- **Step statuses:** `succeeded`, `failed`, `blocked` (a direct or transitive prerequisite did not
  succeed; the record names the blockers; independent branches still run), `not_run` (budget exhausted).
  A result that is not an `AgentSuccess`/`AgentFailure`, whose ids do not match the task, or an agent
  that raises, is a `failed` step with orchestrator failure kind `invalid_result` or `agent_raised`.
- **Task status** is computed from the steps, not set freely: `succeeded` only if every step
  succeeded; `failed` if any step failed or is blocked; otherwise `incomplete`.
- **Retries.** `RetryPolicy(max_attempts=2)`, hard cap 5; a step may lower it. Retry only an
  `AgentFailure` with `retryable=True`. Invalid results, raised exceptions and non-retryable failures
  are never retried. No re-planning. Total attempts are at most steps x cap. Backoff uses an injected
  `sleep` (default: none).
- **Deadline.** No watchdog thread. Optional `run_budget_s`, checked before each attempt against an
  injected monotonic clock; when exhausted, remaining steps are `not_run` and no retry starts.
  A hung call is not interrupted (known limit; clients must honor `timeout_s`).
- **Data flow.** `depends_on` controls ordering and blocking only; inputs are literal. Passing data
  between steps is deferred.
- **CLI.** `cognitive-mesh run PLAN.json --root DIR [--json]`: exit 0 succeeded, 6 invalid plan
  (including unreadable or oversized plan file), 7 failed or incomplete. No model client exists, so
  `explain_file` steps fail as `provider_unavailable`.
- Nothing is persisted (traces are v0.6.0).

## Alternatives considered
- Thread watchdog per call: cannot kill a hung call and may run a call twice on retry.
- Fallback to another capable agent: hides failures; deferred.
- `$ref` data passing between steps: needs a schema-compatibility story; deferred.
- Concurrent execution: unnecessary complexity before there is a real provider.

## Consequences
- Plans are validated up front, so structural errors never produce partial runs.
- The task status cannot disagree with its steps.
- A hung provider call can exceed the run budget; only the clients' own timeouts bound it.
- Steps cannot consume earlier outputs yet; useful multi-step pipelines wait for a later ADR.

## Amendment 2026-10-17: drafting a plan from a request
Accepted by the user before coding: a `plan` command that drafts a plan and does not run it.
- `cognitive-mesh plan "REQUEST" [--task-id ID] [--output FILE]` uses the configured model client (the
  Groq remote-content opt-in applies) and prints or writes plan JSON that `run` accepts. Exit 2 for
  config, 5 if the file cannot be written, 10 (`EXIT_PLAN_GENERATION_ERROR`) when no usable plan came back.
- `orchestrator/generate.py`: the request (max 2,000 characters) and the capability catalog go to the
  model as one JSON string with a system note that the request is untrusted data. The reply must be
  strict JSON of exactly `{"steps": [...]}`; it becomes a `TaskPlan` only through the existing strict
  model and must pass `plan_task` (registered capabilities, valid dependencies and references, no
  cycles). `task_id` is set by us, never by the model.
- Nothing is executed. The output is labelled model-written and unreviewed on stderr. A plan can pass
  validation and still be wrong (wrong inputs, wrong choice of steps); a real run with Ollama
  `llama3.2` produced a structurally valid plan with a nonsensical `node_id`. `run` then fails that
  step visibly; nothing checks plan quality beyond the structure.
- Error messages hold fixed text and plan ids only: never the request or the model's text.
