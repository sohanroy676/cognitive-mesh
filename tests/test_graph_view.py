from pathlib import Path

import pytest

from cognitive_mesh.graph import (
    DependencyGraph,
    Edge,
    EdgeKind,
    Evidence,
    GraphDiagnostic,
    GraphDiagnosticCode,
    Node,
    NodeKind,
    build_dependency_graph,
    build_view,
)
from cognitive_mesh.ingestion import SourceRef, ingest_application

FIXTURES = Path(__file__).parent / "fixtures"


def make_graph(
    nodes: list[tuple[str, NodeKind]],
    edges: list[tuple[str, str, EdgeKind]],
    diagnostics: list[GraphDiagnostic] | None = None,
) -> DependencyGraph:
    ref = SourceRef(root="/abs/root", path="src/x.py", size_bytes=1, sha256="0" * 64)
    evidence = (Evidence(ref=ref, line=1, rule="test:rule", detail="detail"),)
    return DependencyGraph(
        [Node(id=i, kind=k, name=i) for i, k in nodes],
        [Edge(source=s, target=t, kind=k, evidence=evidence) for s, t, k in edges],
        diagnostics or [],
    )


def modules(*names: str) -> list[tuple[str, NodeKind]]:
    return [(n, NodeKind.MODULE) for n in names]


# --- layers and cyclic components ---------------------------------------------------------------


def test_layers_put_dependents_first() -> None:
    g = make_graph(
        modules("a", "b", "c", "d"),
        [("a", "b", EdgeKind.IMPORTS), ("b", "c", EdgeKind.IMPORTS), ("a", "d", EdgeKind.IMPORTS)],
    )
    assert g.layers() == [["a"], ["b", "d"], ["c"]]
    assert g.cyclic_components() == []


def test_cycle_members_share_a_layer() -> None:
    g = make_graph(
        modules("a", "b", "c"),
        [("a", "b", EdgeKind.IMPORTS), ("b", "a", EdgeKind.IMPORTS), ("b", "c", EdgeKind.IMPORTS)],
    )
    assert g.layers() == [["a", "b"], ["c"]]
    assert g.cyclic_components() == [("a", "b")]


def test_self_loop_is_a_cyclic_component() -> None:
    g = make_graph(modules("a", "b"), [("a", "a", EdgeKind.IMPORTS)])
    assert g.cyclic_components() == [("a",)]
    assert g.layers() == [["a", "b"]]


def test_empty_graph_has_no_layers() -> None:
    g = make_graph([], [])
    assert g.layers() == []
    assert g.cyclic_components() == []


# --- selection, numbering and truncation --------------------------------------------------------


def test_view_under_limits_is_complete_and_numbered() -> None:
    g = build_dependency_graph(ingest_application(FIXTURES / "dep_app"))
    view = build_view(g, 100, 100)
    assert not view.truncation.truncated
    assert len(view.nodes) == len(g.nodes())
    assert [e.number for e in view.edges] == list(range(1, len(g.edges()) + 1))
    assert [e.edge for e in view.edges] == g.edges()
    assert {vn.key for vn in view.nodes} == {f"n{i}" for i in range(len(view.nodes))}


def test_view_flags_cycles_unresolved_and_computed() -> None:
    g = build_dependency_graph(ingest_application(FIXTURES / "js_app"))
    view = build_view(g, 100, 100)
    by_id = {vn.node.id: vn for vn in view.nodes}
    computed = [vn for vn in view.nodes if vn.node.id.startswith("unresolved:dynamic:")]
    assert computed
    assert all(vn.style.value == "computed" for vn in computed)
    plain = [vn for vn in view.nodes if vn.node.id.startswith("unresolved:import:")]
    assert plain
    assert all(vn.style.value == "unresolved" for vn in plain)
    assert by_id["component:."].style.value == "component"
    assert any(ve.to_unresolved for ve in view.edges)

    dep = build_view(build_dependency_graph(ingest_application(FIXTURES / "dep_app")), 100, 100)
    cycle_edges = {(e.edge.source, e.edge.target) for e in dep.edges if e.in_cycle}
    assert cycle_edges == {
        ("module:src/dep_app/a.py", "module:src/dep_app/b.py"),
        ("module:src/dep_app/b.py", "module:src/dep_app/a.py"),
    }


def test_truncation_keeps_important_nodes_and_counts_omissions() -> None:
    g = make_graph(
        [("comp", NodeKind.COMPONENT), ("pkg", NodeKind.PACKAGE), ("std", NodeKind.STDLIB)]
        + modules("m1", "m2", "c1", "c2")
        + [("unres", NodeKind.UNRESOLVED)],
        [
            ("c1", "c2", EdgeKind.IMPORTS),
            ("c2", "c1", EdgeKind.IMPORTS),
            ("m1", "m2", EdgeKind.IMPORTS),
            ("m1", "unres", EdgeKind.IMPORTS),
            ("m1", "std", EdgeKind.IMPORTS),
        ],
    )
    view = build_view(g, 5, 100)
    shown = {vn.node.id for vn in view.nodes}
    assert shown == {
        "comp",
        "c1",
        "c2",
        "unres",
        "m1",
    }  # component, cycle, unresolved, then modules
    t = view.truncation
    assert (t.shown_nodes, t.total_nodes) == (5, 8)
    assert t.omitted_nodes_by_kind == {"module": 1, "package": 1, "stdlib": 1}
    assert t.truncated
    assert "showing 5 of 8 nodes" in t.notice()
    assert all(
        ve.edge.source in shown and ve.edge.target in shown for ve in view.edges
    )  # no edge to a hidden node
    assert t.total_edges == 5 and t.shown_edges == 3  # m1->m2 and m1->std hidden with their nodes


def test_edge_limit_prefers_cycle_then_unresolved_edges() -> None:
    g = make_graph(
        modules("a", "b", "c") + [("u", NodeKind.UNRESOLVED)],
        [
            ("a", "b", EdgeKind.IMPORTS),
            ("a", "c", EdgeKind.IMPORTS),
            ("b", "a", EdgeKind.IMPORTS),
            ("c", "u", EdgeKind.IMPORTS),
        ],
    )
    view = build_view(g, 10, 3)
    assert {(ve.edge.source, ve.edge.target) for ve in view.edges} == {
        ("a", "b"),
        ("b", "a"),
        ("c", "u"),
    }
    assert [ve.number for ve in view.edges] == [1, 2, 3]
    assert view.truncation.truncated


@pytest.mark.parametrize(("nodes", "edges"), [(0, 5), (5, 0), (-1, 5)])
def test_view_rejects_non_positive_limits(nodes: int, edges: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        build_view(make_graph([], []), nodes, edges)


def test_large_graph_is_bounded_and_keeps_what_matters() -> None:
    names = [f"m{i:04d}" for i in range(1500)]
    # acyclic (edges only point to higher numbers), so no cycle members outrank the unresolved node
    edges = [(names[i], names[min(i * 7 + 1, 1499)], EdgeKind.IMPORTS) for i in range(1499)]
    edges += [(names[i], names[i + 1], EdgeKind.IMPORTS) for i in range(1499)]
    g = make_graph(
        modules(*names) + [("unresolved:x", NodeKind.UNRESOLVED)],
        [*edges, ("m0000", "unresolved:x", EdgeKind.IMPORTS)],
    )
    view = build_view(g, 200, 400)
    assert len(view.nodes) == 200
    assert len(view.edges) <= 400
    assert view.truncation.truncated
    assert view.truncation.total_nodes == 1501
    assert view.summary["nodes_by_kind"] == {"module": 1500, "unresolved": 1}  # full graph
    assert any(vn.node.id == "unresolved:x" for vn in view.nodes)
    assert build_view(g, 200, 400) == view  # deterministic


def test_view_caps_listed_cycles_and_diagnostics() -> None:
    diags = [
        GraphDiagnostic(
            path=f"p{i}", code=GraphDiagnosticCode.PARSE_ERROR, severity="info", message="m"
        )
        for i in range(250)
    ]
    pairs = [(f"a{i}", f"b{i}") for i in range(60)]
    g = make_graph(
        modules(*[n for pair in pairs for n in pair]),
        [e for a, b in pairs for e in ((a, b, EdgeKind.IMPORTS), (b, a, EdgeKind.IMPORTS))],
        diags,
    )
    view = build_view(g, 500, 500)
    assert len(view.cycles) == 50 and view.cycles_total == 60
    assert len(view.diagnostics) == 200 and view.diagnostics_total == 250
