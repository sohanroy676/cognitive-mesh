"""A bounded, numbered view of a `DependencyGraph` for the renderers (ADR-0008).

Selection and truncation happen here, once, so both renderers show the same thing. The view only
selects from the graph; it never adds, merges or rewrites nodes, edges or evidence.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.graph.graph import DependencyGraph
from cognitive_mesh.graph.models import Edge, GraphDiagnostic, Node, NodeKind, count_values

DEFAULT_MAX_NODES = {"html": 300, "mermaid": 100}
DEFAULT_MAX_EDGES = {"html": 1000, "mermaid": 200}
MAX_CYCLES_LISTED = 50
MAX_DIAGNOSTICS_LISTED = 200
_DYNAMIC_PREFIX = "unresolved:dynamic:"


class NodeStyle(StrEnum):
    COMPONENT = "component"
    MODULE = "module"
    PACKAGE = "package"
    STDLIB = "stdlib"
    UNRESOLVED = "unresolved"
    COMPUTED = "computed"  # an unresolved computed dynamic import (ADR-0007)


STYLE_LABELS = {
    NodeStyle.COMPONENT: "component",
    NodeStyle.MODULE: "module",
    NodeStyle.PACKAGE: "package",
    NodeStyle.STDLIB: "stdlib",
    NodeStyle.UNRESOLVED: "unresolved",
    NodeStyle.COMPUTED: "computed import",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ViewNode(_Frozen):
    key: str  # generated ("n0", ...); graph text is never used as an identifier
    node: Node
    style: NodeStyle
    in_cycle: bool


class ViewEdge(_Frozen):
    number: int  # shown as "e<number>"; stable for a given graph and limits
    edge: Edge
    source_key: str
    target_key: str
    in_cycle: bool
    to_unresolved: bool


class Truncation(_Frozen):
    shown_nodes: int
    total_nodes: int
    shown_edges: int
    total_edges: int
    omitted_nodes_by_kind: dict[str, int]

    @property
    def truncated(self) -> bool:
        return self.shown_nodes < self.total_nodes or self.shown_edges < self.total_edges

    def notice(self) -> str:
        omitted = ", ".join(f"{k}={v}" for k, v in self.omitted_nodes_by_kind.items()) or "none"
        return (
            f"TRUNCATED: showing {self.shown_nodes} of {self.total_nodes} nodes and "
            f"{self.shown_edges} of {self.total_edges} edges; omitted nodes by kind: {omitted}; "
            "use --json for the full graph"
        )


class GraphView(_Frozen):
    nodes: tuple[ViewNode, ...]
    edges: tuple[ViewEdge, ...]
    layers: tuple[tuple[str, ...], ...]  # node keys, dependents first
    truncation: Truncation
    summary: dict[str, object]  # always describes the full graph
    cycles: tuple[tuple[str, ...], ...]  # at most MAX_CYCLES_LISTED
    cycles_total: int
    cycles_truncated: bool
    diagnostics: tuple[GraphDiagnostic, ...]  # at most MAX_DIAGNOSTICS_LISTED
    diagnostics_total: int


def escape_printable(text: str) -> str:
    """Escape control and other non-printable characters so text stays on one visible line."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _style(node: Node) -> NodeStyle:
    if node.kind is NodeKind.UNRESOLVED:
        return NodeStyle.COMPUTED if node.id.startswith(_DYNAMIC_PREFIX) else NodeStyle.UNRESOLVED
    return NodeStyle(node.kind.value)


def _node_priority(style: NodeStyle, in_cycle: bool) -> int:
    if style is NodeStyle.COMPONENT:
        return 0
    if in_cycle:
        return 1
    order = [NodeStyle.COMPUTED, NodeStyle.UNRESOLVED, NodeStyle.MODULE, NodeStyle.PACKAGE]
    return 2 + (order.index(style) if style in order else len(order))


def build_view(graph: DependencyGraph, max_nodes: int, max_edges: int) -> GraphView:
    """Select at most `max_nodes` nodes and `max_edges` edges, favouring what matters most.

    Node priority: components, cycle members, computed imports, unresolved, modules, packages,
    stdlib (ties by id). Edges are kept only between kept nodes: cycle edges first, then edges
    to unresolved nodes, then the rest. Omissions are counted in `truncation`, never silent.
    """
    if max_nodes < 1 or max_edges < 1:
        raise ValueError("max_nodes and max_edges must be at least 1")
    all_nodes = graph.nodes()
    components = graph.cyclic_components()
    component_of = {member: index for index, comp in enumerate(components) for member in comp}
    styles = {n.id: _style(n) for n in all_nodes}

    def in_cycle(node_id: str) -> bool:
        return node_id in component_of

    ranked = sorted(all_nodes, key=lambda n: (_node_priority(styles[n.id], in_cycle(n.id)), n.id))
    kept_nodes = sorted(ranked[:max_nodes], key=lambda n: n.id)
    keys = {n.id: f"n{index}" for index, n in enumerate(kept_nodes)}
    omitted = count_values([styles[n.id].value for n in ranked[max_nodes:]])

    def is_cycle_edge(edge: Edge) -> bool:
        comp = component_of.get(edge.source)
        return comp is not None and comp == component_of.get(edge.target)

    all_edges = graph.edges()
    candidates = [e for e in all_edges if e.source in keys and e.target in keys]

    def edge_priority(edge: Edge) -> int:
        if is_cycle_edge(edge):
            return 0
        return 1 if graph.node(edge.target).kind is NodeKind.UNRESOLVED else 2

    chosen = sorted(range(len(candidates)), key=lambda i: (edge_priority(candidates[i]), i))
    kept_edges = [candidates[i] for i in sorted(chosen[:max_edges])]

    view_nodes = tuple(
        ViewNode(key=keys[n.id], node=n, style=styles[n.id], in_cycle=in_cycle(n.id))
        for n in kept_nodes
    )
    view_edges = tuple(
        ViewEdge(
            number=number,
            edge=e,
            source_key=keys[e.source],
            target_key=keys[e.target],
            in_cycle=is_cycle_edge(e),
            to_unresolved=graph.node(e.target).kind is NodeKind.UNRESOLVED,
        )
        for number, e in enumerate(kept_edges, start=1)
    )
    shown = DependencyGraph(kept_nodes, kept_edges, [])
    layers = tuple(tuple(keys[i] for i in layer) for layer in shown.layers())
    cycles, cycles_truncated = graph.cycles()
    return GraphView(
        nodes=view_nodes,
        edges=view_edges,
        layers=layers,
        truncation=Truncation(
            shown_nodes=len(kept_nodes),
            total_nodes=len(all_nodes),
            shown_edges=len(kept_edges),
            total_edges=len(all_edges),
            omitted_nodes_by_kind=omitted,
        ),
        summary=graph.summary(),
        cycles=tuple(cycles[:MAX_CYCLES_LISTED]),
        cycles_total=len(cycles),
        cycles_truncated=cycles_truncated,
        diagnostics=graph.diagnostics[:MAX_DIAGNOSTICS_LISTED],
        diagnostics_total=len(graph.diagnostics),
    )
