"""Sequential, bounded execution of a validated plan. `Orchestrator.run` never raises (ADR-0010)."""

import time
from collections.abc import Callable

from cognitive_mesh.agents import (
    AgentContext,
    AgentFailure,
    AgentRegistry,
    AgentResult,
    AgentSuccess,
    AgentTask,
)
from cognitive_mesh.orchestrator.models import (
    OrchestratorFailureKind,
    PlanStep,
    RetryPolicy,
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
    ) -> None:
        if run_budget_s is not None and run_budget_s <= 0:
            raise ValueError("run_budget_s must be positive")
        self._registry = registry
        self._policy = policy or RetryPolicy()
        self._budget = run_budget_s
        self._clock = clock
        self._sleep = sleep

    def run(self, plan: ValidatedPlan, context: AgentContext) -> TaskRun:
        start = self._clock()
        status: dict[str, StepStatus] = {}
        records: list[StepRecord] = []
        for step in plan.order:
            record = self._run_step(plan.task_id, step, status, context, start)
            status[step.step_id] = record.status
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
    ) -> StepRecord:
        def record(st: StepStatus, **fields: object) -> StepRecord:
            return StepRecord(step_id=step.step_id, capability=step.capability, status=st, **fields)

        bad = {StepStatus.FAILED, StepStatus.BLOCKED}
        blockers = tuple(sorted(d for d in step.depends_on if status[d] in bad))
        if blockers:
            return record(StepStatus.BLOCKED, blocked_by=blockers)
        if any(status[d] is StepStatus.NOT_RUN for d in step.depends_on):
            return record(StepStatus.NOT_RUN, blocked_by=tuple(sorted(step.depends_on)))
        agents = self._registry.find(step.capability)
        if not agents or not agents[0].supports(step.capability):
            return record(
                StepStatus.FAILED,
                failure_kind=OrchestratorFailureKind.NO_AGENT.value,
                message="no registered agent declares this capability",
            )
        agent = agents[0]
        limit = step.max_attempts or self._policy.max_attempts
        attempts: list[AgentResult] = []
        count = 0
        kind: str | None = None
        message: str | None = None
        while True:
            if self._expired(start):
                if count == 0:
                    return record(StepStatus.NOT_RUN)
                break  # no retry after the deadline; the step stays failed
            count += 1
            agent_task_id = f"{task_id}.{step.step_id}.{count}"
            try:
                result = agent.run(
                    AgentTask(
                        task_id=agent_task_id, capability=step.capability, inputs=step.inputs
                    ),
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
                )
            kind, message = result.kind.value, result.message
            if not result.retryable or count >= limit:
                break
            if self._policy.backoff_s > 0:
                self._sleep(self._policy.backoff_s)
        return record(
            StepStatus.FAILED,
            agent_id=agent.id,
            attempt_count=count,
            attempts=tuple(attempts),
            failure_kind=kind,
            message=message,
        )


def _is_valid(result: object, agent_id: str, task_id: str, capability: str) -> bool:
    return (
        isinstance(result, AgentSuccess | AgentFailure)
        and result.agent_id == agent_id
        and result.task_id == task_id
        and result.capability == capability
    )
