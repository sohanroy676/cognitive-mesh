"""The dependency graph and its traversal API. NetworkX is private to this module."""

from collections import deque
from collections.abc import Collection

import networkx as nx

from cognitive_mesh.graph.builders import build
from cognitive_mesh.graph.models import (
    Edge,
    EdgeKind,
    Evidence,
    GraphDiagnostic,
    GraphError,
    Node,
    NodeKind,
    Reached,
    count_values,
)
from cognitive_mesh.ingestion import IngestionResult


class DependencyGraph:
    """Directed multigraph. An edge `A -> B` means "A depends on / imports B".

    Absence of an edge is not proof of absence of a dependency: see `diagnostics`.
    """

    def __init__(
        self, nodes: list[Node], edges: list[Edge], diagnostics: list[GraphDiagnostic]
    ) -> None:
        self._g: nx.MultiDiGraph[str] = nx.MultiDiGraph()
        for node in nodes:
            self._g.add_node(node.id, node=node)
        for edge in edges:
            self._g.add_edge(edge.source, edge.target, key=edge.kind.value, edge=edge)
        self.diagnostics: tuple[GraphDiagnostic, ...] = tuple(diagnostics)

    def node(self, node_id: str) -> Node:
        if node_id not in self._g:
            raise GraphError(f"unknown node: {node_id!r}")
        found: Node = self._g.nodes[node_id]["node"]
        return found

    def nodes(self, kind: NodeKind | None = None) -> list[Node]:
        items: list[Node] = [data["node"] for _, data in self._g.nodes(data=True)]
        return sorted((n for n in items if kind is None or n.kind is kind), key=lambda n: n.id)

    def edges(self, kind: EdgeKind | None = None) -> list[Edge]:
        items: list[Edge] = [data["edge"] for _, _, data in self._g.edges(data=True)]
        return sorted(
            (e for e in items if kind is None or e.kind is kind),
            key=lambda e: (e.source, e.target, e.kind.value),
        )

    def unresolved(self) -> list[Node]:
        return self.nodes(NodeKind.UNRESOLVED)

    def _out(self, node_id: str, kinds: Collection[EdgeKind] | None) -> list[Edge]:
        self.node(node_id)
        edges: list[Edge] = [d["edge"] for _, _, d in self._g.out_edges(node_id, data=True)]
        return sorted(
            (e for e in edges if kinds is None or e.kind in kinds),
            key=lambda e: (e.target, e.kind.value),
        )

    def _in(self, node_id: str, kinds: Collection[EdgeKind] | None) -> list[Edge]:
        self.node(node_id)
        edges: list[Edge] = [d["edge"] for _, _, d in self._g.in_edges(node_id, data=True)]
        return sorted(
            (e for e in edges if kinds is None or e.kind in kinds),
            key=lambda e: (e.source, e.kind.value),
        )

    def direct_dependencies(
        self, node_id: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        return self._out(node_id, kinds)

    def transitive_dependencies(
        self, node_id: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Reached]:
        """Everything reachable from `node_id`, nearest first. Cycle-safe; start node excluded."""
        return self._walk(node_id, kinds, forward=True)

    def dependents(
        self, node_id: str, *, transitive: bool = False, kinds: Collection[EdgeKind] | None = None
    ) -> list[Reached]:
        """Nodes that depend on `node_id`. `path` runs from the dependent down to `node_id`."""
        reached = self._walk(node_id, kinds, forward=False)
        return reached if transitive else [r for r in reached if r.depth == 1]

    def _walk(
        self, start: str, kinds: Collection[EdgeKind] | None, *, forward: bool
    ) -> list[Reached]:
        self.node(start)
        seen = {start}
        queue: deque[tuple[str, tuple[Edge, ...]]] = deque([(start, ())])
        out: list[Reached] = []
        while queue:
            current, path = queue.popleft()
            for edge in self._out(current, kinds) if forward else self._in(current, kinds):
                nxt = edge.target if forward else edge.source
                if nxt in seen:
                    continue
                seen.add(nxt)
                new_path = (*path, edge) if forward else (edge, *path)
                out.append(Reached(node=self.node(nxt), depth=len(new_path), path=new_path))
                queue.append((nxt, new_path))
        return out

    def path(
        self, source: str, target: str, kinds: Collection[EdgeKind] | None = None
    ) -> tuple[Edge, ...] | None:
        """Shortest chain of edges from `source` to `target`; `()` if equal; None if unreachable."""
        self.node(target)
        if source == target:
            self.node(source)
            return ()
        for reached in self._walk(source, kinds, forward=True):
            if reached.node.id == target:
                return reached.path
        return None

    def cycles(self, limit: int = 1000) -> tuple[list[tuple[str, ...]], bool]:
        """Dependency cycles (self-loops included), canonical and sorted. Second item: truncated."""
        found: list[tuple[str, ...]] = []
        truncated = False
        cycle: list[str]
        for cycle in nx.simple_cycles(nx.DiGraph(self._g)):
            if len(found) >= limit:
                truncated = True
                break
            pivot = cycle.index(min(cycle))
            found.append(tuple(cycle[pivot:] + cycle[:pivot]))
        return sorted(found), truncated

    def cyclic_components(self) -> list[tuple[str, ...]]:
        """Strongly connected components that contain a cycle (size > 1, or a self-loop).

        Unlike `cycles()` this is linear time and has no limit, so it never truncates.
        """
        simple: nx.DiGraph[str] = nx.DiGraph(self._g)
        found = [
            tuple(sorted(comp))
            for comp in nx.strongly_connected_components(simple)
            if len(comp) > 1 or any(simple.has_edge(n, n) for n in comp)
        ]
        return sorted(found)

    def layers(self) -> list[list[str]]:
        """Node ids grouped by depth: dependents first, dependencies after.

        Layers are the topological generations of the cycle-condensed graph, so members of one
        cycle share a layer. Each layer is sorted by id.
        """
        dag = nx.condensation(nx.DiGraph(self._g))
        return [
            sorted(n for comp in generation for n in dag.nodes[comp]["members"])
            for generation in nx.topological_generations(dag)
        ]

    def evidence_for(self, source: str, target: str, kind: EdgeKind) -> tuple[Evidence, ...]:
        self.node(source)
        self.node(target)
        data = self._g.get_edge_data(source, target, key=kind.value)
        if data is None:
            return ()
        edge: Edge = data["edge"]
        return edge.evidence

    def summary(self) -> dict[str, object]:
        cycles, truncated = self.cycles()
        return {
            "nodes_by_kind": count_values([n.kind.value for n in self.nodes()]),
            "edges_by_kind": count_values([e.kind.value for e in self.edges()]),
            "unresolved": len(self.unresolved()),
            "cycles": len(cycles),
            "cycles_truncated": truncated,
            "diagnostics_by_code": count_values([d.code.value for d in self.diagnostics]),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [n.model_dump(mode="json") for n in self.nodes()],
            "edges": [e.model_dump(mode="json") for e in self.edges()],
            "diagnostics": [d.model_dump(mode="json") for d in self.diagnostics],
            "summary": self.summary(),
        }


def build_dependency_graph(result: IngestionResult) -> DependencyGraph:
    """Build the graph from ingestion output only; never reads the disk."""
    nodes, edges, diagnostics = build(result)
    return DependencyGraph(nodes, edges, diagnostics)
