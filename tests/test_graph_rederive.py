"""`rederive` works on a bare dependency graph and does not depend on `experience` (ADR-0017)."""

import ast
from pathlib import Path

import pytest

from cognitive_mesh.graph import DependencyGraph, build_dependency_graph
from cognitive_mesh.graph import rederive as rederive_module
from cognitive_mesh.graph.rederive import Holding, rederive
from cognitive_mesh.ingestion import ingest_application

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
UTIL = "module:src/dep_app/util.py"


@pytest.fixture(scope="module")
def graph() -> DependencyGraph:
    return build_dependency_graph(ingest_application(FIXTURE))


def test_holds_with_evidence(graph: DependencyGraph) -> None:
    got = rederive(graph, "depends_on", A, B, "imports")
    assert got.holding is Holding.HOLDS
    assert got.evidence and got.evidence[0].path == "src/dep_app/a.py"


def test_refuted_when_the_edge_is_missing_or_of_another_kind(graph: DependencyGraph) -> None:
    assert rederive(graph, "depends_on", A, UTIL, "imports").holding is Holding.REFUTED
    assert rederive(graph, "depends_on", A, B, "contains").holding is Holding.REFUTED
    assert rederive(graph, "depends_on", A, None, "imports").holding is Holding.REFUTED


def test_absent_subject_and_unknown_predicate_never_validate(graph: DependencyGraph) -> None:
    assert rederive(graph, "depends_on", "module:gone.py", B, "imports").holding is (
        Holding.SUBJECT_ABSENT
    )
    assert rederive(graph, "is_fine", A, B, "imports").holding is Holding.SUBJECT_ABSENT


def test_unresolved_predicate(graph: DependencyGraph) -> None:
    unresolved = graph.unresolved()[0]
    assert rederive(graph, "is_unresolved", unresolved.id, None, None).holding is Holding.HOLDS
    assert rederive(graph, "is_unresolved", A, None, None).holding is Holding.REFUTED


def test_the_module_imports_nothing_from_experience() -> None:
    tree = ast.parse(Path(rederive_module.__file__).read_text(encoding="utf-8"))
    modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    ]
    assert not [m for m in modules if "experience" in m or "traces" in m or "agents" in m]
