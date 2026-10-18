"""Declared task state between steps (ADR-0023). No network."""

from typing import Any

import pytest
from pydantic import BaseModel

from cognitive_mesh.agents import (
    Agent,
    AgentContext,
    AgentRegistry,
    Basis,
    Capability,
    Handled,
)
from cognitive_mesh.orchestrator import (
    InputRef,
    Orchestrator,
    OrchestratorFailureKind,
    PlanError,
    PlanStep,
    StateEntry,
    StateRef,
    StepStatus,
    TaskPlan,
    TaskRun,
    plan_task,
)
from test_orchestrator_data import LyingProducer, Producer, RaisingProducer, registry_of

CTX = AgentContext()


class Bag(BaseModel):
    data: dict[str, Any]


class Seen(BaseModel):
    keys: list[str]


class BagConsumer(Agent):
    id = "bag_consumer"
    role = "test"
    description = "takes a whole mapping"

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

        def handler(inp: Bag, ctx: AgentContext) -> Handled:
            self.seen.append(inp.data)
            return Handled(Seen(keys=sorted(inp.data)))

        super().__init__([Capability("take_bag", "c", Bag, Seen, handler)])


def use(state: str, step_id: str = "c", *depends: str) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        capability="take_bag",
        depends_on=depends or ("p",),
        input_refs={"data": StateRef(state=state)},
    )


def execute(registry: AgentRegistry, state: dict[str, StateEntry], *steps: PlanStep) -> TaskRun:
    plan = plan_task(TaskPlan(task_id="t", steps=steps, state=state), registry)
    return Orchestrator(registry).run(plan, CTX)


def produce() -> PlanStep:
    return PlanStep(step_id="p", capability="produce")


def test_a_whole_output_reaches_a_later_step_and_its_source_is_recorded() -> None:
    consumer = BagConsumer()
    run = execute(
        registry_of(Producer(), consumer), {"made": StateEntry(step_id="p")}, produce(), use("made")
    )
    assert run.steps[1].status is StepStatus.SUCCEEDED
    assert consumer.seen == [{"value": "hello", "count": 3}]
    (source,) = run.steps[1].input_sources
    assert (source.state, source.step_id, source.field) == ("made", "p", "*")
    assert source.basis is Basis.DETERMINISTIC


def test_only_the_declared_fields_are_passed() -> None:
    consumer = BagConsumer()
    state = {"n": StateEntry(step_id="p", fields=("count",))}
    run = execute(registry_of(Producer(), consumer), state, produce(), use("n"))
    assert consumer.seen == [{"count": 3}]
    assert run.steps[1].input_sources[0].field == "count"


def test_a_model_written_source_stays_model_unverified() -> None:
    consumer = BagConsumer()
    run = execute(
        registry_of(Producer(model=True), consumer),
        {"made": StateEntry(step_id="p")},
        produce(),
        use("made"),
    )
    source = run.steps[1].input_sources[0]
    assert source.basis is Basis.MODEL_UNVERIFIED and source.model is not None


def test_state_survives_an_intermediate_step() -> None:
    consumer = BagConsumer()
    middle = PlanStep(step_id="m", capability="produce", depends_on=("p",))
    run = execute(
        registry_of(Producer(), consumer),
        {"made": StateEntry(step_id="p")},
        produce(),
        middle,
        use("made", "c", "p", "m"),
    )
    assert [s.status for s in run.steps] == [StepStatus.SUCCEEDED] * 3
    assert consumer.seen == [{"value": "hello", "count": 3}]


def test_a_failed_source_blocks_the_step_and_nothing_is_defaulted() -> None:
    consumer = BagConsumer()
    run = execute(
        registry_of(RaisingProducer(), consumer),
        {"made": StateEntry(step_id="p")},
        produce(),
        use("made"),
    )
    assert run.steps[1].status is StepStatus.BLOCKED and consumer.seen == []


def test_a_missing_declared_field_fails_the_step_with_names_only() -> None:
    consumer = BagConsumer()
    state = {"v": StateEntry(step_id="p", fields=("value",))}
    run = execute(registry_of(LyingProducer(), consumer), state, produce(), use("v"))
    step = run.steps[1]
    assert step.status is StepStatus.FAILED
    assert step.failure_kind == OrchestratorFailureKind.INVALID_REFERENCE.value
    assert step.message is not None and "value" in step.message and "hello" not in step.message
    assert consumer.seen == []


@pytest.mark.parametrize(
    ("state", "step", "needle"),
    [
        ({}, use("nope"), "undeclared state"),
        ({"s": StateEntry(step_id="zz")}, use("s"), "unknown step"),
        ({"s": StateEntry(step_id="p", fields=())}, use("s"), "distinct fields"),
        ({"s": StateEntry(step_id="p", fields=("count", "count"))}, use("s"), "distinct fields"),
        ({"s": StateEntry(step_id="p", fields=("ghost",))}, use("s"), "does not declare"),
        ({"bad name": StateEntry(step_id="p")}, use("s"), "invalid name"),
    ],
)
def test_invalid_state_is_refused_before_anything_runs(
    state: dict[str, StateEntry], step: PlanStep, needle: str
) -> None:
    registry = registry_of(Producer(), BagConsumer())
    with pytest.raises(PlanError, match=needle):
        plan_task(TaskPlan(task_id="t", steps=(produce(), step), state=state), registry)


def test_a_state_reference_needs_a_dependency_on_the_source_step() -> None:
    registry = registry_of(Producer(), BagConsumer())
    step = PlanStep(
        step_id="c",
        capability="take_bag",
        input_refs={"data": StateRef(state="made")},
    )
    plan = TaskPlan(task_id="t", steps=(produce(), step), state={"made": StateEntry(step_id="p")})
    with pytest.raises(PlanError, match="does not depend on"):
        plan_task(plan, registry)


def test_a_state_name_cannot_also_be_a_literal_input() -> None:
    registry = registry_of(Producer(), BagConsumer())
    step = use("made").model_copy(update={"inputs": {"data": {"x": 1}}})
    plan = TaskPlan(task_id="t", steps=(produce(), step), state={"made": StateEntry(step_id="p")})
    with pytest.raises(PlanError, match="both as a literal and as a reference"):
        plan_task(plan, registry)


def test_a_plan_without_state_behaves_as_before() -> None:
    plan = TaskPlan(task_id="t", steps=(produce(),))
    assert plan.state == {}
    run = Orchestrator(registry_of(Producer())).run(plan_task(plan, registry_of(Producer())), CTX)
    assert run.steps[0].input_sources == ()


def test_plain_references_and_state_references_can_be_mixed() -> None:
    assert InputRef(step_id="p", field="value").field == "value"
    parsed = TaskPlan.model_validate(
        {
            "task_id": "t",
            "state": {"made": {"step_id": "p", "fields": "*"}},
            "steps": [
                {"step_id": "p", "capability": "produce"},
                {
                    "step_id": "c",
                    "capability": "take_bag",
                    "depends_on": ["p"],
                    "input_refs": {"data": {"state": "made"}},
                },
            ],
        }
    )
    assert isinstance(parsed.steps[1].input_refs["data"], StateRef)
