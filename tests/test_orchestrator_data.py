"""Data passing between steps and fallback agents (ADR-0010 amendment). No network."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from cognitive_mesh.agents import (
    Agent,
    AgentContext,
    AgentFault,
    AgentRegistry,
    AgentResult,
    AgentSuccess,
    AgentTask,
    Basis,
    Capability,
    FailureKind,
    Handled,
    ModelInfo,
)
from cognitive_mesh.orchestrator import (
    InputRef,
    Orchestrator,
    OrchestratorFailureKind,
    PlanError,
    PlanStep,
    RetryPolicy,
    StepStatus,
    TaskPlan,
    TaskRun,
    plan_task,
)
from cognitive_mesh.traces import TraceRecorder, TraceRecordError, TraceStore

CTX = AgentContext()


class Nothing(BaseModel):
    pass


class Made(BaseModel):
    value: str
    count: int


class Wanted(BaseModel):
    text: str
    suffix: str = ""


class Echoed(BaseModel):
    echo: str


class Producer(Agent):
    id = "producer"
    role = "test"
    description = "produces a value"

    def __init__(self, value: str = "hello", *, model: bool = False) -> None:
        self.calls = 0
        info = ModelInfo(provider="fake", model="m") if model else None

        def handler(_: Nothing, ctx: AgentContext) -> Handled:
            self.calls += 1
            return Handled(Made(value=value, count=3), model=info)

        super().__init__([Capability("produce", "p", Nothing, Made, handler, uses_model=model)])


class Consumer(Agent):
    role = "test"
    description = "consumes a value"

    def __init__(
        self, agent_id: str = "consumer", *, fail: bool = False, retryable: bool = False
    ) -> None:
        self.id = agent_id
        self.seen: list[Wanted] = []

        def handler(inp: Wanted, ctx: AgentContext) -> Handled:
            self.seen.append(inp)
            if fail:
                raise AgentFault(FailureKind.PROVIDER_UNAVAILABLE, "down", retryable=retryable)
            return Handled(Echoed(echo=inp.text + inp.suffix))

        super().__init__([Capability("consume", "c", Wanted, Echoed, handler)])


class RaisingConsumer(Consumer):
    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        raise RuntimeError("boom")


class RaisingProducer(Producer):
    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        raise RuntimeError("boom")


class LyingProducer(Producer):
    """Reports success but without the field its schema promises."""

    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        result = super().run(task, context)
        assert isinstance(result, AgentSuccess)
        return result.model_copy(update={"output": {"other": "x"}})


def registry_of(*agents: Agent) -> AgentRegistry:
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return registry


def produce(step_id: str = "p") -> PlanStep:
    return PlanStep(step_id=step_id, capability="produce")


def consume(step_id: str = "c", *, src: str = "p", field: str = "value", **kw: object) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        capability="consume",
        depends_on=(src,),
        input_refs={"text": InputRef(step_id=src, field=field)},
        **kw,
    )


def execute(registry: AgentRegistry, *steps: PlanStep, **kw: object) -> TaskRun:
    plan = plan_task(TaskPlan(task_id="t", steps=steps), registry)
    return Orchestrator(registry, **kw).run(plan, CTX)  # type: ignore[arg-type]


# -- data passing ------------------------------------------------------------------------------


def test_a_later_step_receives_an_earlier_steps_output_field() -> None:
    consumer = Consumer()
    run = execute(registry_of(Producer("hi there"), consumer), produce(), consume())
    assert [s.status for s in run.steps] == [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]
    assert consumer.seen == [Wanted(text="hi there")]
    last = run.steps[1].attempts[-1]
    assert isinstance(last, AgentSuccess)
    assert last.output == {"echo": "hi there"}


def test_literal_inputs_and_references_combine() -> None:
    consumer = Consumer()
    execute(registry_of(Producer("a"), consumer), produce(), consume(inputs={"suffix": "!"}))
    assert consumer.seen == [Wanted(text="a", suffix="!")]


def test_the_source_of_a_resolved_input_is_recorded_with_its_basis() -> None:
    run = execute(registry_of(Producer(model=True), Consumer()), produce(), consume())
    (source,) = run.steps[1].input_sources
    assert (source.input, source.step_id, source.field) == ("text", "p", "value")
    assert source.basis is Basis.MODEL_UNVERIFIED
    assert source.model == ModelInfo(provider="fake", model="m")


def test_a_deterministic_source_is_recorded_as_such() -> None:
    run = execute(registry_of(Producer(), Consumer()), produce(), consume())
    assert run.steps[1].input_sources[0].basis is Basis.DETERMINISTIC


def test_a_reference_of_the_wrong_type_fails_the_consumer_not_the_run() -> None:
    run = execute(registry_of(Producer(), Consumer()), produce(), consume(field="count"))
    assert run.steps[0].status is StepStatus.SUCCEEDED
    assert run.steps[1].status is StepStatus.FAILED
    assert run.steps[1].failure_kind == FailureKind.INVALID_INPUT.value


def test_a_failed_producer_blocks_the_consumer() -> None:
    run = execute(registry_of(RaisingProducer(), Consumer()), produce(), consume())
    assert [s.status for s in run.steps] == [StepStatus.FAILED, StepStatus.BLOCKED]


def test_a_missing_field_at_run_time_is_an_explicit_failure() -> None:
    run = execute(registry_of(LyingProducer(), Consumer()), produce(), consume())
    assert run.steps[1].status is StepStatus.FAILED
    assert run.steps[1].failure_kind == OrchestratorFailureKind.INVALID_REFERENCE.value


def _no_dependency() -> list[PlanStep]:
    ref = {"text": InputRef(step_id="p", field="value")}
    return [produce(), PlanStep(step_id="c", capability="consume", input_refs=ref)]


def _unknown_step() -> list[PlanStep]:
    ref = {"text": InputRef(step_id="zz", field="value")}
    return [PlanStep(step_id="c", capability="consume", depends_on=("zz",), input_refs=ref)]


@pytest.mark.parametrize(
    ("make", "needle"),
    [
        (_no_dependency, "does not depend on"),
        (lambda: [produce(), consume(field="nope")], "does not declare"),
        (lambda: [produce(), consume(inputs={"text": "lit"})], "both as a literal"),
        (_unknown_step, "unknown steps"),
    ],
    ids=["no-dependency", "unknown-field", "literal-and-ref", "unknown-step"],
)
def test_bad_references_are_rejected_before_anything_runs(make: object, needle: str) -> None:
    producer = Producer()
    registry = registry_of(producer, Consumer())
    steps = tuple(make())  # type: ignore[operator]
    with pytest.raises(PlanError, match=needle):
        plan_task(TaskPlan(task_id="t", steps=steps), registry)
    assert producer.calls == 0


def test_hostile_reference_names_are_rejected_without_echoing_them() -> None:
    hostile = "x\x1b[31m<script>"
    step = PlanStep(
        step_id="c",
        capability="consume",
        depends_on=("p",),
        input_refs={hostile: InputRef(step_id="p", field="value")},
    )
    plan = TaskPlan(task_id="t", steps=(produce(), step))
    with pytest.raises(PlanError) as err:
        plan_task(plan, registry_of(Producer(), Consumer()))
    assert "<script>" not in str(err.value)
    assert "\x1b" not in str(err.value)


@pytest.mark.parametrize("field", ["", "a b", "a;b", "../x", "x" * 65])
def test_reference_fields_are_pattern_restricted(field: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        InputRef(step_id="p", field=field)


def test_a_reference_cycle_is_still_a_plan_error() -> None:
    def looped(me: str, other: str) -> PlanStep:
        ref = {"text": InputRef(step_id=other, field="echo")}
        return PlanStep(step_id=me, capability="consume", depends_on=(other,), input_refs=ref)

    plan = TaskPlan(task_id="t", steps=(looped("a", "b"), looped("b", "a")))
    with pytest.raises(PlanError, match="cycle"):
        plan_task(plan, registry_of(Consumer()))


# -- fallback agents -----------------------------------------------------------------------------


def test_a_fallback_agent_takes_over_after_a_failure_result() -> None:
    first, second = Consumer("a-first", fail=True), Consumer("b-second")
    run = execute(registry_of(Producer(), first, second), produce(), consume())
    step = run.steps[1]
    assert step.status is StepStatus.SUCCEEDED
    assert step.agent_id == "b-second"
    assert [a.agent_id for a in step.attempts] == ["a-first", "b-second"]
    assert [a.task_id for a in step.attempts] == ["t.c.1", "t.c.2"]
    assert step.attempt_count == 2


def test_each_agent_gets_its_own_retry_budget() -> None:
    first = Consumer("a-first", fail=True, retryable=True)
    second = Consumer("b-second", fail=True, retryable=True)
    run = execute(
        registry_of(Producer(), first, second),
        produce(),
        consume(),
        policy=RetryPolicy(max_attempts=2),
    )
    step = run.steps[1]
    assert step.status is StepStatus.FAILED
    assert (len(first.seen), len(second.seen)) == (2, 2)
    assert step.attempt_count == 4
    assert step.agent_id == "b-second"


def test_at_most_three_agents_are_tried() -> None:
    agents = [Consumer(f"a{i}", fail=True) for i in range(5)]
    run = execute(registry_of(Producer(), *agents), produce(), consume())
    assert run.steps[1].attempt_count == 3
    assert [len(a.seen) for a in agents] == [1, 1, 1, 0, 0]


def test_a_raising_agent_does_not_trigger_a_fallback() -> None:
    second = Consumer("b-second")
    run = execute(registry_of(Producer(), RaisingConsumer("a-first"), second), produce(), consume())
    assert run.steps[1].failure_kind == OrchestratorFailureKind.AGENT_RAISED.value
    assert second.seen == []


def test_a_first_agent_success_never_calls_the_fallback() -> None:
    second = Consumer("b-second")
    run = execute(registry_of(Producer(), Consumer("a-first"), second), produce(), consume())
    assert run.steps[1].agent_id == "a-first"
    assert second.seen == []


# -- traces ------------------------------------------------------------------------------------


def test_a_fallback_run_is_stored_and_read_back_in_a_new_connection(tmp_path: Path) -> None:
    registry = registry_of(Producer(), Consumer("a-first", fail=True), Consumer("b-second"))
    plan = plan_task(TaskPlan(task_id="t", steps=(produce(), consume())), registry)
    run = Orchestrator(registry).run(plan, CTX)
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        recorder = TraceRecorder(store)
        run_id = recorder.begin(plan, None)
        recorder.finish(run_id, run)
    with TraceStore.open_existing(db) as store:
        trace = store.get(run_id)
    step = trace.steps[1]
    assert step.agent_id == "b-second"
    assert [a.agent_id for a in step.attempts] == ["a-first", "b-second"]
    assert trace.to_task_run().status == run.status
    # The plan keeps the reference, so the data flow stays inspectable.
    assert "input_refs" in str(trace.plan)


def test_a_stored_step_whose_last_attempt_names_another_agent_is_corrupt(tmp_path: Path) -> None:
    registry = registry_of(Producer(), Consumer("a-first", fail=True), Consumer("b-second"))
    plan = plan_task(TaskPlan(task_id="t", steps=(produce(), consume())), registry)
    run = Orchestrator(registry).run(plan, CTX)
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        recorder = TraceRecorder(store)
        run_id = recorder.begin(plan, None)
        recorder.finish(run_id, run)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE steps SET agent_id = 'a-first' WHERE step_id = 'c'")
    conn.commit()
    conn.close()
    with TraceStore.open_existing(db) as store, pytest.raises(TraceRecordError):
        store.get(run_id)
