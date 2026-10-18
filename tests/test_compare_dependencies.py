"""`compare_dependencies`: a deterministic consumer of declared task state (ADR-0024)."""

from pathlib import Path

import pytest

from cognitive_mesh.agents import (
    AgentContext,
    AgentFailure,
    AgentSuccess,
    AgentTask,
    Basis,
    DependencyAnalyst,
    FailureKind,
    default_registry,
)
from cognitive_mesh.agents.dependency_analyst import CompareDependenciesOutput
from cognitive_mesh.experience import build_from_traces
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import (
    Orchestrator,
    PlanStep,
    StateEntry,
    StateRef,
    StepStatus,
    TaskPlan,
    plan_task,
)
from cognitive_mesh.traces import TraceRecorder, TraceStore

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
C = "module:src/dep_app/c.py"
UTIL = "module:src/dep_app/util.py"


def report(node: str, *targets: str, truncated: bool = False) -> dict[str, object]:
    return {
        "node_id": node,
        "transitive": False,
        "total": len(targets),
        "truncated": truncated,
        "dependencies": [{"node_id": t, "depth": 1} for t in targets],
    }


def compare(first: object, second: object, **kw: object) -> AgentSuccess | AgentFailure:
    task = AgentTask.model_validate(
        {
            "task_id": "t",
            "capability": "compare_dependencies",
            "inputs": {"first": first, "second": second, **kw},
        }
    )
    return DependencyAnalyst().run(task, AgentContext())


def output(result: AgentSuccess | AgentFailure) -> CompareDependenciesOutput:
    assert isinstance(result, AgentSuccess)
    return CompareDependenciesOutput.model_validate(result.output)


def test_shared_and_exclusive_dependencies_are_separated_and_sorted() -> None:
    result = compare(report(B, A, UTIL), report(C, UTIL, "x"))
    assert isinstance(result, AgentSuccess)
    assert result.basis is Basis.DETERMINISTIC and result.model is None
    out = output(result)
    assert (out.first_node, out.second_node) == (B, C)
    assert out.shared == [UTIL] and out.only_first == [A] and out.only_second == ["x"]
    assert not out.truncated and not out.inputs_truncated


def test_identical_disjoint_and_empty_reports() -> None:
    same = output(compare(report(A, B), report(A, B)))
    assert same.shared == [B] and same.only_first == [] and same.only_second == []
    apart = output(compare(report(A, B), report(C, UTIL)))
    assert apart.shared == [] and apart.only_first == [B] and apart.only_second == [UTIL]
    empty = output(compare(report(A), report(C)))
    assert empty.shared_total == empty.only_first_total == empty.only_second_total == 0


def test_duplicate_targets_count_once() -> None:
    out = output(compare(report(A, B, B), report(C, B)))
    assert out.shared == [B] and out.shared_total == 1


def test_the_limit_cuts_lists_but_not_the_totals() -> None:
    many = [f"n{i}" for i in range(10)]
    out = output(compare(report(A, *many), report(C), limit=3))
    assert len(out.only_first) == 3 and out.only_first_total == 10 and out.truncated


def test_an_input_report_that_was_cut_off_is_flagged() -> None:
    out = output(compare(report(A, B, truncated=True), report(C, B)))
    assert out.inputs_truncated and not out.truncated


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"node_id": A},
        {"node_id": 3, "dependencies": []},
        {"node_id": A, "dependencies": "nope"},
        {"node_id": A, "dependencies": [{"depth": 1}]},
        {"node_id": A, "dependencies": ["x"]},
        {"node_id": A, "dependencies": [{"node_id": 7}]},
    ],
)
def test_a_malformed_report_fails_naming_the_input_not_its_content(bad: object) -> None:
    for first, second, name in ((bad, report(C), "first"), (report(C), bad, "second")):
        result = compare(first, second)
        assert isinstance(result, AgentFailure)
        assert result.kind is FailureKind.INVALID_INPUT and name in result.message
        assert "dep_app" not in result.message


def test_a_non_object_input_is_refused() -> None:
    result = compare("not an object", report(A))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.INVALID_INPUT


def stateful_plan() -> TaskPlan:
    def deps(step_id: str, node: str) -> PlanStep:
        return PlanStep(step_id=step_id, capability="dependencies_of", inputs={"node_id": node})

    return TaskPlan(
        task_id="cmp",
        state={"a": StateEntry(step_id="da"), "b": StateEntry(step_id="db")},
        steps=(
            deps("da", B),
            deps("db", C),
            PlanStep(
                step_id="cmp",
                capability="compare_dependencies",
                depends_on=("da", "db"),
                input_refs={"first": StateRef(state="a"), "second": StateRef(state="b")},
            ),
        ),
    )


def run_plan(plan: TaskPlan) -> tuple[object, object]:
    ingestion = ingest_application(FIXTURE)
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    registry = default_registry()
    validated = plan_task(plan, registry)
    return validated, Orchestrator(registry).run(validated, context)


def test_a_two_step_plan_compares_real_dependencies_through_state() -> None:
    _, run = run_plan(stateful_plan())
    last = run.steps[-1]  # type: ignore[attr-defined]
    assert last.status is StepStatus.SUCCEEDED
    out = CompareDependenciesOutput.model_validate(last.attempts[-1].output)
    assert (
        out.shared == [UTIL]
        and out.only_first == [A]
        and "unresolved:import:yaml" in out.only_second
    )
    assert {s.state for s in last.input_sources} == {"a", "b"}
    assert all(s.basis is Basis.DETERMINISTIC for s in last.input_sources)


def test_a_failed_first_step_blocks_the_comparison() -> None:
    plan = stateful_plan()
    bad = plan.steps[0].model_copy(update={"inputs": {"node_id": "module:nope.py"}})
    _, run = run_plan(plan.model_copy(update={"steps": (bad, *plan.steps[1:])}))
    statuses = [s.status for s in run.steps]  # type: ignore[attr-defined]
    assert statuses == [StepStatus.FAILED, StepStatus.SUCCEEDED, StepStatus.BLOCKED]


def test_a_comparison_never_becomes_a_claim(tmp_path: Path) -> None:
    ingestion = ingest_application(FIXTURE)
    validated, run = run_plan(stateful_plan())
    with TraceStore.create(tmp_path / "t.sqlite3") as store:
        recorder = TraceRecorder(store)
        run_id = recorder.begin(validated, ingestion)  # type: ignore[arg-type]
        recorder.finish(run_id, run)  # type: ignore[arg-type]
        snapshot = build_from_traces(store)
    claims = [n for n in snapshot.nodes if n.kind == "claim"]
    # only the two dependencies_of steps make claims; the comparison adds none and no evidence
    assert claims and all("compare" not in (c.detail or "") for c in claims)
    from_compare = [
        e for e in snapshot.edges if e.kind == "evidenced_by" and "cmp" in str(e.target)
    ]
    assert from_compare == []
