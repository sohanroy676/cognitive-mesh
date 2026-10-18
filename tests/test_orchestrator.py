"""Orchestrator tests: planning, ordering, failures, retries, budget (ADR-0010). No network."""

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from cognitive_mesh.agents import (
    Agent,
    AgentContext,
    AgentFailure,
    AgentRegistry,
    AgentResult,
    AgentSuccess,
    AgentTask,
    Basis,
    Capability,
    FailureKind,
    Handled,
    default_registry,
)
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import (
    MAX_STEPS,
    Orchestrator,
    OrchestratorFailureKind,
    PlanError,
    PlanStep,
    RetryPolicy,
    StepRecord,
    StepStatus,
    TaskPlan,
    TaskRun,
    TaskStatus,
    ValidatedPlan,
    plan_task,
)
from cognitive_mesh.providers import ModelRequest, ModelResponse, ProviderError, ProviderErrorKind

FIXTURES = Path(__file__).parent / "fixtures"
HOSTILE = "SECRET-MARKER-<script>\x1b[31m"
CTX = AgentContext()


class _Empty(BaseModel):
    pass


def ok(task: AgentTask, agent_id: str) -> AgentSuccess:
    return AgentSuccess(
        agent_id=agent_id,
        task_id=task.task_id,
        capability=task.capability,
        output={},
        basis=Basis.DETERMINISTIC,
    )


def fail(task: AgentTask, agent_id: str, *, retryable: bool = False) -> AgentFailure:
    return AgentFailure(
        agent_id=agent_id,
        task_id=task.task_id,
        capability=task.capability,
        kind=FailureKind.PROVIDER_UNAVAILABLE,
        message="the model provider is unavailable",
        retryable=retryable,
    )


Script = Callable[[AgentTask, int], object]


class Scripted(Agent):
    """An agent whose `run` is driven by a script: (task, call number) -> result or exception."""

    def __init__(self, agent_id: str, caps: list[str], script: Script | None = None) -> None:
        self.id = agent_id
        self.role = "test"
        self.description = "scripted test agent"
        self.script = script or (lambda task, n: ok(task, agent_id))
        self.calls: list[str] = []
        super().__init__(
            [
                Capability(
                    name=c,
                    description="test",
                    input_model=_Empty,
                    output_model=_Empty,
                    handler=lambda i, ctx: Handled(_Empty()),
                )
                for c in caps
            ]
        )

    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        self.calls.append(task.task_id)
        outcome = self.script(task, len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


def registry_of(*agents: Agent) -> AgentRegistry:
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return registry


def step(step_id: str, *deps: str, cap: str = "work", **kw: object) -> PlanStep:
    return PlanStep(step_id=step_id, capability=cap, depends_on=deps, **kw)


def plan_of(*steps: PlanStep, task_id: str = "t") -> TaskPlan:
    return TaskPlan(task_id=task_id, steps=steps)


def run(
    agent: Agent,
    *steps: PlanStep,
    policy: RetryPolicy | None = None,
    **kw: object,
) -> TaskRun:
    registry = registry_of(agent)
    orch = Orchestrator(registry, policy, **kw)  # type: ignore[arg-type]
    return orch.run(plan_task(plan_of(*steps), registry), CTX)


def by_id(result: TaskRun) -> dict[str, StepRecord]:
    return {s.step_id: s for s in result.steps}


# ---- success and ordering -------------------------------------------------------------------


def test_single_step_succeeds_and_is_inspectable() -> None:
    agent = Scripted("a", ["work"])
    result = run(agent, step("only"))
    assert result.status is TaskStatus.SUCCEEDED
    record = result.steps[0]
    assert (record.status, record.agent_id, record.attempt_count) == (StepStatus.SUCCEEDED, "a", 1)
    assert isinstance(record.attempts[0], AgentSuccess)
    assert agent.calls == ["t.only.1"]


def test_dependent_steps_run_after_prerequisites_even_if_declared_first() -> None:
    agent = Scripted("a", ["work"])
    result = run(agent, step("c", "b"), step("b", "a"), step("a"))
    assert agent.calls == ["t.a.1", "t.b.1", "t.c.1"]
    assert [s.step_id for s in result.steps] == ["a", "b", "c"]
    assert result.status is TaskStatus.SUCCEEDED


def test_order_is_deterministic_and_ties_follow_declaration_order() -> None:
    steps = (step("z"), step("m", "z"), step("a", "z"), step("b"))
    registry = registry_of(Scripted("a", ["work"]))
    orders = {
        tuple(s.step_id for s in plan_task(plan_of(*steps), registry).order) for _ in range(5)
    }
    assert orders == {("z", "m", "a", "b")}


def test_diamond_runs_join_last() -> None:
    agent = Scripted("a", ["work"])
    run(
        agent, step("join", "left", "right"), step("left", "top"), step("right", "top"), step("top")
    )
    assert agent.calls[0] == "t.top.1" and agent.calls[-1] == "t.join.1"


def test_real_agents_run_a_dependent_plan_on_a_fixture() -> None:
    ingestion = ingest_application(FIXTURES / "dep_app")
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    registry = default_registry()
    plan = plan_of(
        PlanStep(
            step_id="deps",
            capability="dependencies_of",
            inputs={"node_id": "module:src/dep_app/a.py"},
            depends_on=("report",),
        ),
        PlanStep(step_id="report", capability="unresolved_report"),
    )
    result = Orchestrator(registry).run(plan_task(plan, registry), context)
    assert result.status is TaskStatus.SUCCEEDED
    assert [s.step_id for s in result.steps] == ["report", "deps"]


def test_assignment_is_by_capability_and_deterministic() -> None:
    first, second, other = Scripted("a1", ["work"]), Scripted("a2", ["work"]), Scripted("b", ["x"])
    registry = registry_of(second, other, first)
    Orchestrator(registry).run(plan_task(plan_of(step("s")), registry), CTX)
    assert (first.calls, second.calls, other.calls) == (["t.s.1"], [], [])


# ---- invalid plans: nothing runs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "match"),
    [
        ((), "no steps"),
        ((step("a"), step("a")), "duplicate step ids: a"),
        ((step("a", "a"),), "depends on itself"),
        ((step("a", "ghost"),), "unknown steps: ghost"),
        ((step("a", "b"), step("b", "a")), "cycle among steps: a, b"),
        ((step("a", "b"), step("b", "c"), step("c", "a"), step("d")), "cycle among steps: a, b, c"),
        ((step("a", cap="nope"),), "capability 'nope'"),
        (tuple(step(f"s{i}") for i in range(MAX_STEPS + 1)), "more than"),
    ],
    ids=["empty", "dup", "self", "missing", "cycle2", "cycle3", "capability", "too_many"],
)
def test_invalid_plans_raise_before_anything_runs(steps: tuple[PlanStep, ...], match: str) -> None:
    agent = Scripted("a", ["work"])
    with pytest.raises(PlanError, match=match):
        plan_task(plan_of(*steps), registry_of(agent))
    assert agent.calls == []


def test_missing_dependency_does_not_run_the_valid_steps() -> None:
    agent = Scripted("a", ["work"])
    with pytest.raises(PlanError):
        plan_task(plan_of(step("ok"), step("bad", "ghost")), registry_of(agent))
    assert agent.calls == []


def test_models_reject_hostile_ids_and_extra_keys_and_loose_types() -> None:
    with pytest.raises(ValidationError):
        PlanStep(step_id=HOSTILE, capability="work")
    with pytest.raises(ValidationError):
        PlanStep(step_id="a", capability="Work")
    with pytest.raises(ValidationError):
        PlanStep.model_validate({"step_id": "a", "capability": "work", "bogus": 1})
    with pytest.raises(ValidationError):
        TaskPlan.model_validate_json('{"task_id": 5, "steps": []}', strict=True)
    with pytest.raises(ValidationError):
        PlanStep(step_id="a", capability="work", max_attempts=6)
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=6)
    with pytest.raises(ValueError, match="positive"):
        Orchestrator(AgentRegistry(), run_budget_s=0)


# ---- failures, blocked steps, invalid results -----------------------------------------------


def test_failure_blocks_dependents_but_independent_branches_still_run() -> None:
    def script(task: AgentTask, n: int) -> AgentResult:
        return fail(task, "a") if ".bad." in task.task_id else ok(task, "a")

    agent = Scripted("a", ["work"], script)
    result = run(agent, step("bad"), step("child", "bad"), step("grand", "child"), step("free"))
    rec = by_id(result)
    assert rec["bad"].status is StepStatus.FAILED
    assert rec["bad"].failure_kind == FailureKind.PROVIDER_UNAVAILABLE.value
    assert rec["child"].status is StepStatus.BLOCKED and rec["child"].blocked_by == ("bad",)
    assert rec["grand"].status is StepStatus.BLOCKED and rec["grand"].blocked_by == ("child",)
    assert rec["free"].status is StepStatus.SUCCEEDED
    assert result.status is TaskStatus.FAILED
    assert not any(".child." in c or ".grand." in c for c in agent.calls)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda task, n: "just a string", id="wrong_type"),
        pytest.param(lambda task, n: None, id="none"),
        pytest.param(
            lambda task, n: ok(task.model_copy(update={"task_id": "other"}), "a"), id="task_id"
        ),
        pytest.param(
            lambda task, n: ok(task.model_copy(update={"capability": "zzz"}), "a"), id="cap"
        ),
        pytest.param(lambda task, n: ok(task, "impostor"), id="agent_id"),
    ],
)
def test_invalid_results_fail_the_step_and_are_not_retried(bad: Script) -> None:
    agent = Scripted("a", ["work"], bad)
    result = run(agent, step("s"), step("next", "s"), policy=RetryPolicy(max_attempts=4))
    rec = by_id(result)
    assert rec["s"].status is StepStatus.FAILED
    assert rec["s"].failure_kind == OrchestratorFailureKind.INVALID_RESULT.value
    assert rec["s"].attempt_count == 1 and rec["s"].attempts == ()
    assert rec["next"].status is StepStatus.BLOCKED
    assert result.status is TaskStatus.FAILED
    assert len(agent.calls) == 1


def test_a_raising_agent_fails_the_step_without_leaking_exception_text() -> None:
    agent = Scripted("a", ["work"], lambda task, n: RuntimeError(HOSTILE))
    result = run(agent, step("s"))
    rec = result.steps[0]
    assert rec.status is StepStatus.FAILED
    assert rec.failure_kind == OrchestratorFailureKind.AGENT_RAISED.value
    assert rec.message == "the agent raised RuntimeError"
    assert HOSTILE not in result.model_dump_json()
    assert len(agent.calls) == 1


def test_capability_vanishing_from_the_registry_is_a_failure_not_a_crash() -> None:
    registry = registry_of(Scripted("a", ["work"]))
    validated = plan_task(plan_of(step("s")), registry)
    result = Orchestrator(AgentRegistry()).run(validated, CTX)
    assert result.steps[0].failure_kind == OrchestratorFailureKind.NO_AGENT.value
    assert result.status is TaskStatus.FAILED


def test_hostile_input_names_are_refused_and_never_echoed() -> None:
    registry = default_registry()
    plan = plan_of(
        PlanStep(step_id="s", capability="dependencies_of", inputs={"node_id": 5, HOSTILE: 1})
    )
    with pytest.raises(PlanError) as refused:
        plan_task(plan, registry)
    assert HOSTILE not in str(refused.value)


# ---- unavailable providers ------------------------------------------------------------------


def _explain_run(client: object | None, policy: RetryPolicy | None = None) -> TaskRun:
    registry = default_registry(client)  # type: ignore[arg-type]
    plan = plan_of(
        PlanStep(step_id="e", capability="explain_file", inputs={"path": "pyproject.toml"}),
        PlanStep(step_id="after", capability="unresolved_report", depends_on=("e",)),
    )
    context = AgentContext(ingestion=ingest_application(FIXTURES / "sample_app"))
    return Orchestrator(registry, policy).run(plan_task(plan, registry), context)


def test_no_model_client_fails_the_task_and_blocks_dependents() -> None:
    result = _explain_run(None)
    rec = by_id(result)
    assert rec["e"].failure_kind == FailureKind.PROVIDER_UNAVAILABLE.value
    assert rec["e"].attempt_count == 1  # not retryable: nothing to retry against
    assert rec["after"].status is StepStatus.BLOCKED
    assert result.status is TaskStatus.FAILED


class _Down:
    provider = "fake"
    model = "m"

    def __init__(self, kind: ProviderErrorKind) -> None:
        self.kind = kind
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        raise ProviderError(self.kind, "api key sk-LEAK")


def test_unavailable_provider_is_retried_a_bounded_number_of_times_then_fails() -> None:
    client = _Down(ProviderErrorKind.UNAVAILABLE)
    result = _explain_run(client, RetryPolicy(max_attempts=3))
    assert client.calls == 3
    assert by_id(result)["e"].attempt_count == 3
    assert by_id(result)["e"].status is StepStatus.FAILED
    assert result.status is TaskStatus.FAILED
    assert "sk-LEAK" not in result.model_dump_json()


def test_auth_error_is_not_retried() -> None:
    client = _Down(ProviderErrorKind.AUTH)
    result = _explain_run(client, RetryPolicy(max_attempts=5))
    assert client.calls == 1 and result.status is TaskStatus.FAILED


# ---- retries ---------------------------------------------------------------------------------


def test_retryable_failure_then_success_is_a_success_with_both_attempts_recorded() -> None:
    def script(task: AgentTask, n: int) -> AgentResult:
        return fail(task, "a", retryable=True) if n == 1 else ok(task, "a")

    agent = Scripted("a", ["work"], script)
    result = run(agent, step("s"))
    rec = result.steps[0]
    assert rec.status is StepStatus.SUCCEEDED and rec.attempt_count == 2
    assert [type(r) for r in rec.attempts] == [AgentFailure, AgentSuccess]
    assert agent.calls == ["t.s.1", "t.s.2"]
    assert result.total_attempts == 2


def test_always_retryable_failure_stops_exactly_at_the_limit() -> None:
    agent = Scripted("a", ["work"], lambda task, n: fail(task, "a", retryable=True))
    result = run(agent, step("s"), policy=RetryPolicy(max_attempts=4))
    assert len(agent.calls) == 4
    assert result.steps[0].status is StepStatus.FAILED
    assert result.status is TaskStatus.FAILED


def test_step_can_lower_the_attempt_limit_and_non_retryable_gets_one_attempt() -> None:
    always = Scripted("a", ["work"], lambda task, n: fail(task, "a", retryable=True))
    run(always, step("s", max_attempts=1), policy=RetryPolicy(max_attempts=5))
    assert len(always.calls) == 1
    never = Scripted("b", ["work"], lambda task, n: fail(task, "b", retryable=False))
    run(never, step("s"), policy=RetryPolicy(max_attempts=5))
    assert len(never.calls) == 1


def test_total_attempts_are_bounded_by_steps_times_cap() -> None:
    agent = Scripted("a", ["work"], lambda task, n: fail(task, "a", retryable=True))
    steps = [step(f"s{i}") for i in range(10)]
    result = run(agent, *steps, policy=RetryPolicy(max_attempts=5))
    assert result.total_attempts == len(agent.calls) == 50


def test_backoff_uses_the_injected_sleep_only_between_attempts() -> None:
    sleeps: list[float] = []
    agent = Scripted("a", ["work"], lambda task, n: fail(task, "a", retryable=True))
    run(agent, step("s"), policy=RetryPolicy(max_attempts=3, backoff_s=0.5), sleep=sleeps.append)
    assert sleeps == [0.5, 0.5]


# ---- run budget ------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_exhausted_budget_leaves_remaining_steps_not_run_and_the_task_incomplete() -> None:
    clock = _Clock()

    def script(task: AgentTask, n: int) -> AgentResult:
        clock.now += 10
        return ok(task, "a")

    agent = Scripted("a", ["work"], script)
    result = run(
        agent, step("a1"), step("a2", "a1"), step("a3", "a2"), step("solo"),
        run_budget_s=15, clock=clock,
    )  # fmt: skip
    rec = by_id(result)
    assert rec["a1"].status is StepStatus.SUCCEEDED
    assert rec["a2"].status is StepStatus.SUCCEEDED  # started at t=10 < 15
    assert rec["a3"].status is StepStatus.NOT_RUN and rec["solo"].status is StepStatus.NOT_RUN
    assert result.status is TaskStatus.INCOMPLETE
    assert len(agent.calls) == 2


def test_dependents_of_a_not_run_step_are_not_run_not_blocked() -> None:
    clock = _Clock()

    def script(task: AgentTask, n: int) -> AgentResult:
        clock.now += 5
        return ok(task, "a")

    agent = Scripted("a", ["work"], script)
    result = run(
        agent, step("first"), step("x", "first"), step("y", "x"), run_budget_s=1, clock=clock
    )
    assert [s.status for s in result.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.NOT_RUN,
        StepStatus.NOT_RUN,
    ]
    assert result.status is TaskStatus.INCOMPLETE


def test_no_retry_starts_after_the_deadline_and_the_step_stays_failed() -> None:
    clock = _Clock()

    def script(task: AgentTask, n: int) -> AgentResult:
        clock.now += 10
        return fail(task, "a", retryable=True)

    agent = Scripted("a", ["work"], script)
    result = run(agent, step("s"), run_budget_s=5, clock=clock)
    assert len(agent.calls) == 1
    assert result.steps[0].status is StepStatus.FAILED
    assert result.status is TaskStatus.FAILED


# ---- the task status can never hide a failure --------------------------------------------------


@pytest.mark.parametrize("statuses", list(itertools.product(StepStatus, repeat=3)))
def test_task_is_succeeded_only_when_every_step_succeeded(statuses: tuple[StepStatus, ...]) -> None:
    steps = tuple(
        StepRecord(step_id=f"s{i}", capability="work", status=s) for i, s in enumerate(statuses)
    )
    task = TaskRun(task_id="t", steps=steps)
    all_ok = all(s is StepStatus.SUCCEEDED for s in statuses)
    assert (task.status is TaskStatus.SUCCEEDED) == all_ok
    if StepStatus.FAILED in statuses or StepStatus.BLOCKED in statuses:
        assert task.status is TaskStatus.FAILED
    assert task.model_dump(mode="json")["status"] == task.status.value


def test_an_empty_run_is_never_reported_as_succeeded() -> None:
    assert TaskRun(task_id="t", steps=()).status is TaskStatus.INCOMPLETE


def test_validated_plan_is_what_the_runner_takes() -> None:
    registry = registry_of(Scripted("a", ["work"]))
    assert isinstance(plan_task(plan_of(step("s")), registry), ValidatedPlan)
