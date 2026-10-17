"""Traversal over the experience graph (ADR-0015)."""

from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    EdgeKind,
    ExpEdge,
    ExperienceNodeNotFoundError,
    ExpNode,
    NodeKind,
    Snapshot,
    TraceRef,
    build_from_traces,
)
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db

A, B, C, D = "module:a.py", "module:b.py", "module:c.py", "module:d.py"


@pytest.fixture
def snap(tmp_path: Path) -> Snapshot:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B, C], [D])], "fp1"),
            "r2": ([deps_step("r2", "s1", A, [B])], "fp2"),
        },
    )
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def claim_named(graph: ExperienceGraph, name: str) -> ExpNode:
    return next(n for n in graph.claims() if n.name == name)


def test_a_claim_reaches_its_evidence_subject_and_object(snap: Snapshot) -> None:
    graph = ExperienceGraph(snap)
    claim = claim_named(graph, f"{A} depends_on {B}")
    direct = {r.node.id for r in graph.reachable(claim.id)}
    assert direct == {"attempt:r1/s1/1", "attempt:r2/s1/1", f"target:{A}", f"target:{B}"}
    deep = {r.node.id for r in graph.reachable(claim.id, transitive=True)}
    assert {"capability:dependencies_of", "agent:agent"} <= deep
    assert all(r.depth >= 1 for r in graph.reachable(claim.id, transitive=True))


def test_indirect_traversal_returns_shortest_paths_nearest_first(snap: Snapshot) -> None:
    graph = ExperienceGraph(snap)
    claim = claim_named(graph, f"{A} depends_on {C}")
    reached = graph.reachable(claim.id, transitive=True)
    assert [r.depth for r in reached] == sorted(r.depth for r in reached)
    cap = next(r for r in reached if r.node.kind is NodeKind.CAPABILITY)
    assert cap.depth == 2
    assert [e.kind for e in cap.path] == [EdgeKind.EVIDENCED_BY, EdgeKind.USED_CAPABILITY]


def test_backward_traversal_finds_what_rests_on_a_target(snap: Snapshot) -> None:
    graph = ExperienceGraph(snap)
    # C is only the object of one claim; edges run claim -> attempt, so nothing else rests on it
    back_c = graph.reachable(f"target:{C}", forward=False, transitive=True)
    assert [(r.node.kind, r.node.name) for r in back_c] == [(NodeKind.CLAIM, f"{A} depends_on {C}")]
    # A is the subject of both claims and the target of both attempts
    back_a = {r.node.id for r in graph.reachable(f"target:{A}", forward=False)}
    assert {"attempt:r1/s1/1", "attempt:r2/s1/1"} <= back_a
    assert len([i for i in back_a if i.startswith("claim:")]) == 2


def test_transitive_dependencies_of_the_analysed_code_are_not_graph_targets(snap: Snapshot) -> None:
    assert not ExperienceGraph(snap).has_node(f"target:{D}")


def test_cycles_do_not_loop(snap: Snapshot) -> None:
    ref = TraceRef(run_id="r")
    nodes = tuple(ExpNode(id=i, kind=NodeKind.TARGET, name=i) for i in ("x", "y"))
    edges = (
        ExpEdge(source="x", target="y", kind=EdgeKind.ABOUT, refs=(ref,)),
        ExpEdge(source="y", target="x", kind=EdgeKind.ABOUT, refs=(ref,)),
    )
    graph = ExperienceGraph(Snapshot(nodes=nodes, edges=edges))
    assert [r.node.id for r in graph.reachable("x", transitive=True)] == ["y"]


def test_unknown_node_and_non_claim_evidence_raise(snap: Snapshot) -> None:
    graph = ExperienceGraph(snap)
    with pytest.raises(ExperienceNodeNotFoundError):
        graph.reachable("nope")
    with pytest.raises(ExperienceNodeNotFoundError):
        graph.node("nope")
    with pytest.raises(ExperienceNodeNotFoundError, match="not a claim"):
        graph.evidence("run:r1")


def test_evidence_lists_every_attempt_that_stated_the_claim(snap: Snapshot) -> None:
    graph = ExperienceGraph(snap)
    ab = graph.evidence(claim_named(graph, f"{A} depends_on {B}").id)
    ac = graph.evidence(claim_named(graph, f"{A} depends_on {C}").id)
    assert [e.target for e in ab] == ["attempt:r1/s1/1", "attempt:r2/s1/1"]
    assert [e.target for e in ac] == ["attempt:r1/s1/1"]


def test_query_order_does_not_depend_on_insertion_order(snap: Snapshot) -> None:
    shuffled = Snapshot(nodes=tuple(reversed(snap.nodes)), edges=tuple(reversed(snap.edges)))
    one, two = ExperienceGraph(snap), ExperienceGraph(shuffled)
    assert one.nodes() == two.nodes()
    assert one.edges() == two.edges()
    start = one.claims()[0].id
    assert one.reachable(start, transitive=True) == two.reachable(start, transitive=True)


def test_summary_counts_nodes_and_edges_by_kind(snap: Snapshot) -> None:
    summary = ExperienceGraph(snap).summary()
    assert summary["nodes"]["claim"] == 2
    assert summary["nodes"]["run"] == 2
    assert summary["edges"]["evidenced_by"] == 3  # AB twice (r1, r2), AC once
