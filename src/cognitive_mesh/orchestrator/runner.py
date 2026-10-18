"""Sequential, bounded execution of a validated plan. `Orchestrator.run` never raises (ADR-0010)."""

import time
from collections.abc import Callable
from datetime import datetime

from pydantic import JsonValue

from cognitive_mesh.agents import (
    AgentContext,
    AgentFailure,
    AgentRegistry,
    AgentResult,
    AgentSuccess,
    AgentTask,
)
from cognitive_mesh.orchestrator.models import (
    MAX_AGENTS_PER_STEP,
    InputSource,
    OrchestratorFailureKind,
    PlanStep,
    RetryPolicy,
    StateEntry,
    StateRef,
    StepRecord,
    StepStatus,
    TaskRun,
)
from cognitive_mesh.orchestrator.planner import ValidatedPlan


class Orchestrator:
    def __init__(
        self,
        registry: AgentRegistry,
        policy: RetryPolicy | None = None,
        *,
        run_budget_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if run_budget_s is not None and run_budget_s <= 0:
            raise ValueError("run_budget_s must be positive")
        self._registry = registry
        self._policy = policy or RetryPolicy()
        self._budget = run_budget_s
        self._clock = clock
        self._sleep = sleep
        self._now = now

    def run(self, plan: ValidatedPlan, context: AgentContext) -> TaskRun:
        start = self._clock()
        status: dict[str, StepStatus] = {}
        outputs: dict[str, AgentSuccess] = {}
        records: list[StepRecord] = []
        for step in plan.order:
            started = self._now() if self._now else None
            record = self._run_step(plan.task_id, step, status, context, start, outputs, plan.state)
            if self._now is not None:
                record = record.model_copy(
                    update={"started_at": started, "finished_at": self._now()}
                )
            status[step.step_id] = record.status
            if record.status is StepStatus.SUCCEEDED and isinstance(
                record.attempts[-1], AgentSuccess
            ):
                outputs[step.step_id] = record.attempts[-1]
            records.append(record)
        return TaskRun(task_id=plan.task_id, steps=tuple(records))

    def _expired(self, start: float) -> bool:
        return self._budget is not None and self._clock() - start >= self._budget

    def _run_step(
        self,
        task_id: str,
        step: PlanStep,
        status: dict[str, StepStatus],
        context: AgentContext,
        start: float,
        outputs: dict[str, AgentSuccess],
        state: dict[str, StateEntry],
    ) -> StepRecord:
        def record(st: StepStatus, **fields: object) -> StepRecord:
            return StepRecord(step_id=step.step_id, capability=step.capability, status=st, **fields)

        bad = {StepStatus.FAILED, StepStatus.BLOCKED}
        blockers = tuple(sorted(d for d in step.depends_on if status[d] in bad))
        if blockers:
            return record(StepStatus.BLOCKED, blocked_by=blockers)
        if any(status[d] is StepStatus.NOT_RUN for d in step.depends_on):
            return record(StepStatus.NOT_RUN, blocked_by=tuple(sorted(step.depends_on)))
        agents = [a for a in self._registry.find(step.capability) if a.supports(step.capability)][
            :MAX_AGENTS_PER_STEP
        ]
        if not agents:
            return record(
                StepStatus.FAILED,
                failure_kind=OrchestratorFailureKind.NO_AGENT.value,
                message="no registered agent declares this capability",
            )
        resolved = _resolve_inputs(step, outputs, state)
        if isinstance(resolved, str):
            return record(
                StepStatus.FAILED,
                failure_kind=OrchestratorFailureKind.INVALID_REFERENCE.value,
                message=resolved,
            )
        inputs, sources = resolved
        limit = step.max_attempts or self._policy.max_attempts
        attempts: list[AgentResult] = []
        count = 0
        kind: str | None = None
        message: str | None = None
        used_agent = agents[0]
        for agent in agents:  # later agents are fallbacks, tried only after a failure result
            used_agent = agent
            tries = 0
            fell_through = False
            while True:
                if self._expired(start):
                    if count == 0:
                        return record(StepStatus.NOT_RUN)
                    break  # no retry after the deadline; the step stays failed
                count += 1
                tries += 1
                agent_task_id = f"{task_id}.{step.step_id}.{count}"
                try:
                    result = agent.run(
                        AgentTask(task_id=agent_task_id, capability=step.capability, inputs=inputs),
                        context,
                    )
                except Exception as exc:  # noqa: BLE001 - a third-party agent must not crash the run
                    kind = OrchestratorFailureKind.AGENT_RAISED.value
                    message = f"the agent raised {type(exc).__name__}"
                    break
                if not _is_valid(result, agent.id, agent_task_id, step.capability):
                    kind = OrchestratorFailureKind.INVALID_RESULT.value
                    message = "the agent returned a result that does not match the task"
                    break
                attempts.append(result)
                if isinstance(result, AgentSuccess):
                    return record(
                        StepStatus.SUCCEEDED,
                        agent_id=agent.id,
                        attempt_count=count,
                        attempts=tuple(attempts),
                        input_sources=sources,
                    )
                kind, message = result.kind.value, result.message
                if not result.retryable or tries >= limit:
                    fell_through = True  # an ordinary failure result: a fallback may try
                    break
                if self._policy.backoff_s > 0:
                    self._sleep(self._policy.backoff_s)
            if not fell_through:
                break  # raised, invalid, or out of time: a defect or deadline, not a fallback case
        return record(
            StepStatus.FAILED,
            agent_id=used_agent.id,
            attempt_count=count,
            attempts=tuple(attempts),
            failure_kind=kind,
            message=message,
            input_sources=sources,
        )


def _resolve_state(
    name: str, input_name: str, entry: StateEntry, outputs: dict[str, AgentSuccess]
) -> tuple[JsonValue, InputSource] | str:
    """The value of a declared state entry, or a failure message. State is never defaulted."""
    upstream = outputs.get(entry.step_id)
    if upstream is None:
        return (
            f"input {input_name!r} needs state {name!r}, but step {entry.step_id!r} "
            "produced no output"
        )
    if entry.fields == "*":
        value: dict[str, JsonValue] = dict(upstream.output)
        label = "*"
    else:
        missing = [f for f in entry.fields if f not in upstream.output]
        if missing:
            return (
                f"input {input_name!r} needs state {name!r}, but step {entry.step_id!r} "
                f"has no output field(s): {', '.join(missing)}"
            )
        value = {f: upstream.output[f] for f in entry.fields}
        label = ",".join(entry.fields)
    source = InputSource(
        input=input_name,
        step_id=entry.step_id,
        field=label,
        basis=upstream.basis,
        model=upstream.model,
        state=name,
    )
    return value, source


def _resolve_inputs(
    step: PlanStep, outputs: dict[str, AgentSuccess], state: dict[str, StateEntry]
) -> tuple[dict[str, JsonValue], tuple[InputSource, ...]] | str:
    """Literal inputs plus values taken from earlier outputs; a string is a failure message."""
    inputs: dict[str, JsonValue] = dict(step.inputs)
    sources: list[InputSource] = []
    for name, ref in sorted(step.input_refs.items()):
        if isinstance(ref, StateRef):
            entry = state.get(ref.state)
            if entry is None:
                return f"input {name!r} references undeclared state {ref.state!r}"
            taken = _resolve_state(ref.state, name, entry, outputs)
            if isinstance(taken, str):
                return taken
            inputs[name], source = taken
            sources.append(source)
            continue
        upstream = outputs.get(ref.step_id)
        if upstream is None or ref.field not in upstream.output:
            return (
                f"input {name!r} could not be taken from field {ref.field!r} "
                f"of step {ref.step_id!r}"
            )
        inputs[name] = upstream.output[ref.field]
        sources.append(
            InputSource(
                input=name,
                step_id=ref.step_id,
                field=ref.field,
                basis=upstream.basis,
                model=upstream.model,
            )
        )
    return inputs, tuple(sources)


def _is_valid(result: object, agent_id: str, task_id: str, capability: str) -> bool:
    return (
        isinstance(result, AgentSuccess | AgentFailure)
        and result.agent_id == agent_id
        and result.task_id == task_id
        and result.capability == capability
    )
