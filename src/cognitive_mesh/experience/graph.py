"""Queries over the experience graph (ADR-0015). The only module that imports `networkx`.

The graph is rebuilt in memory from a checked `Snapshot`; it is never the database. Every query
returns the frozen models, sorted, so output does not depend on insertion order.
"""

from collections import deque
from collections.abc import Collection

import networkx as nx

from cognitive_mesh.experience.models import (
    EdgeKind,
    ExpEdge,
    ExperienceNodeNotFoundError,
    ExpNode,
    NodeKind,
    Reached,
    Snapshot,
)


class ExperienceGraph:
    def __init__(self, snapshot: Snapshot) -> None:
        self._g: nx.MultiDiGraph[str] = nx.MultiDiGraph()
        for node in snapshot.nodes:
            self._g.add_node(node.id, node=node)
        for edge in snapshot.edges:
            self._g.add_edge(edge.source, edge.target, key=edge.kind.value, edge=edge)

    def __len__(self) -> int:
        return int(self._g.number_of_nodes())

    def has_node(self, node_id: str) -> bool:
        return bool(self._g.has_node(node_id))

    def node(self, node_id: str) -> ExpNode:
        if not self._g.has_node(node_id):
            raise ExperienceNodeNotFoundError("unknown node")
        node: ExpNode = self._g.nodes[node_id]["node"]
        return node

    def nodes(self, kind: NodeKind | None = None) -> list[ExpNode]:
        found: list[ExpNode] = [data["node"] for _, data in self._g.nodes(data=True)]
        return sorted((n for n in found if kind is None or n.kind is kind), key=lambda n: n.id)

    def edges(self) -> list[ExpEdge]:
        found: list[ExpEdge] = [data["edge"] for _, _, data in self._g.edges(data=True)]
        return sorted(found, key=lambda e: (e.source, e.target, e.kind.value))

    def out_edges(self, node_id: str, kinds: Collection[EdgeKind] | None = None) -> list[ExpEdge]:
        self.node(node_id)
        found: list[ExpEdge] = [d["edge"] for _, _, d in self._g.out_edges(node_id, data=True)]
        return self._pick(found, kinds)

    def in_edges(self, node_id: str, kinds: Collection[EdgeKind] | None = None) -> list[ExpEdge]:
        self.node(node_id)
        found: list[ExpEdge] = [d["edge"] for _, _, d in self._g.in_edges(node_id, data=True)]
        return self._pick(found, kinds)

    @staticmethod
    def _pick(edges: list[ExpEdge], kinds: Collection[EdgeKind] | None) -> list[ExpEdge]:
        keep = [e for e in edges if kinds is None or e.kind in kinds]
        return sorted(keep, key=lambda e: (e.source, e.target, e.kind.value))

    def reachable(
        self,
        start: str,
        *,
        forward: bool = True,
        transitive: bool = False,
        kinds: Collection[EdgeKind] | None = None,
    ) -> list[Reached]:
        """Nodes reachable from `start`, nearest first, cycle-safe. `path` keeps edge direction."""
        self.node(start)
        seen = {start}
        queue: deque[tuple[str, tuple[ExpEdge, ...]]] = deque([(start, ())])
        out: list[Reached] = []
        while queue:
            current, path = queue.popleft()
            step = self.out_edges(current, kinds) if forward else self.in_edges(current, kinds)
            for edge in step:
                nxt = edge.target if forward else edge.source
                if nxt in seen:
                    continue
                seen.add(nxt)
                new_path = (*path, edge) if forward else (edge, *path)
                out.append(Reached(node=self.node(nxt), depth=len(new_path), path=new_path))
                queue.append((nxt, new_path))
        return out if transitive else [r for r in out if r.depth == 1]

    def claims(self) -> list[ExpNode]:
        return self.nodes(NodeKind.CLAIM)

    def evidence(self, claim_id: str) -> list[ExpEdge]:
        """The `evidenced_by` edges of a claim: one per attempt that stated it."""
        if self.node(claim_id).kind is not NodeKind.CLAIM:
            raise ExperienceNodeNotFoundError("the node is not a claim")
        return self.out_edges(claim_id, {EdgeKind.EVIDENCED_BY})

    def summary(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = {}
        for n in self.nodes():
            nodes[n.kind.value] = nodes.get(n.kind.value, 0) + 1
        edges: dict[str, int] = {}
        for e in self.edges():
            edges[e.kind.value] = edges.get(e.kind.value, 0) + 1
        return {"nodes": dict(sorted(nodes.items())), "edges": dict(sorted(edges.items()))}
