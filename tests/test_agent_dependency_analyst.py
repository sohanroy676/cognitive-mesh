from pathlib import Path

from cognitive_mesh.agents import (
    AgentContext,
    AgentFailure,
    AgentSuccess,
    AgentTask,
    Basis,
    DependencyAnalyst,
    FailureKind,
)
from cognitive_mesh.agents.dependency_analyst import DependenciesOfOutput, UnresolvedReportOutput
from cognitive_mesh.graph import DependencyGraph, build_dependency_graph
from cognitive_mesh.ingestion import ingest_application

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"


def _graph() -> DependencyGraph:
    return build_dependency_graph(ingest_application(FIXTURE))


def _run(capability: str, inputs: dict[str, object]) -> AgentSuccess | AgentFailure:
    task = AgentTask.model_validate({"task_id": "t", "capability": capability, "inputs": inputs})
    return DependencyAnalyst().run(task, AgentContext(graph=_graph()))


def _deps(result: AgentSuccess | AgentFailure) -> DependenciesOfOutput:
    assert isinstance(result, AgentSuccess)
    return DependenciesOfOutput.model_validate(result.output)


def test_direct_dependencies_match_the_graph_with_evidence() -> None:
    result = _run("dependencies_of", {"node_id": A})
    assert isinstance(result, AgentSuccess)
    assert result.basis is Basis.DETERMINISTIC and result.model is None
    expected = _graph().direct_dependencies(A)
    out = _deps(result)
    assert [i.node_id for i in out.dependencies] == [e.target for e in expected]
    assert all(i.evidence for i in out.dependencies)
    assert out.total == len(expected) and out.truncated is False
    assert result.evidence
    assert all(len(e.sha256) == 64 for e in result.evidence)


def test_transitive_dependencies_match_the_graph() -> None:
    out = _deps(_run("dependencies_of", {"node_id": A, "transitive": True}))
    reached = _graph().transitive_dependencies(A)
    assert [(i.node_id, i.depth) for i in out.dependencies] == [
        (r.node.id, r.depth) for r in reached
    ]
    assert max(i.depth for i in out.dependencies) > 1


def test_limit_sets_truncated_and_total_stays_full() -> None:
    out = _deps(_run("dependencies_of", {"node_id": A, "transitive": True, "limit": 1}))
    assert out.truncated is True
    assert out.total == len(_graph().transitive_dependencies(A))
    assert len(out.dependencies) == 1


def test_a_file_path_names_its_module_node() -> None:
    out = _deps(_run("dependencies_of", {"node_id": "src/dep_app/a.py"}))
    assert out.node_id == A
    assert [i.node_id for i in out.dependencies] == [
        e.target for e in _graph().direct_dependencies(A)
    ]


def test_a_path_that_names_no_module_is_still_invalid_input() -> None:
    result = _run("dependencies_of", {"node_id": "src/dep_app/missing.py"})
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.INVALID_INPUT


def test_unknown_node_is_invalid_input() -> None:
    result = _run("dependencies_of", {"node_id": "module:nope.py"})
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.INVALID_INPUT
    assert "nope" not in result.message


def test_unresolved_report_matches_the_graph() -> None:
    result = _run("unresolved_report", {})
    assert isinstance(result, AgentSuccess)
    graph = _graph()
    out = UnresolvedReportOutput.model_validate(result.output)
    assert [i.node_id for i in out.unresolved] == [n.id for n in graph.unresolved()]
    assert out.total == len(graph.unresolved())
    assert sum(out.diagnostics_by_code.values()) == len(graph.diagnostics)


def test_unresolved_report_limit_truncates() -> None:
    result = _run("unresolved_report", {"limit": 1})
    assert isinstance(result, AgentSuccess)
    out = UnresolvedReportOutput.model_validate(result.output)
    assert out.truncated is True
    assert len(out.unresolved) == 1
