"""The dependency graph as JSON for the interactive page (ADR-0032 points 3 and 4).

The layout is the server-side layered layout the HTML page uses. Evidence is path, line and rule
only: no cited file text leaves the server (ADR-0032 point 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from cognitive_mesh.graph import DependencyGraph, build_view
from cognitive_mesh.graph.render_html import BOX_H, BOX_W, layout_positions
from cognitive_mesh.ui.errors import ApiError

HTML_MAX_NODES: Final = 300
HTML_MAX_EDGES: Final = 1000
FULL_MAX_NODES: Final = 3000
FULL_MAX_EDGES: Final = 10000


def graph_payload(graph: DependencyGraph, folder: Path, *, full: bool) -> dict[str, Any]:
    total_nodes, total_edges = len(graph.nodes()), len(graph.edges())
    if full:
        if total_nodes > FULL_MAX_NODES or total_edges > FULL_MAX_EDGES:
            raise ApiError(
                409,
                f"the full graph has {total_nodes} nodes and {total_edges} edges; the limit is "
                f"{FULL_MAX_NODES} nodes and {FULL_MAX_EDGES} edges. "
                "Run `cognitive-mesh graph DIR --json` for all of it.",
            )
        view = build_view(graph, max(total_nodes, 1), max(total_edges, 1))
    else:
        view = build_view(graph, HTML_MAX_NODES, HTML_MAX_EDGES)
    positions, width, height = layout_positions(view)
    trunc = view.truncation
    return {
        "folder": str(folder),
        "full": full,
        "width": width,
        "height": height,
        "box": {"w": BOX_W, "h": BOX_H},
        "nodes": [
            {
                "key": n.key,
                "id": n.node.id,
                "kind": n.node.kind.value,
                "shape": n.style.value,
                "label": n.node.name,
                "path": n.node.ref.path if n.node.ref else None,
                "in_cycle": n.in_cycle,
                "x": positions[n.key][0],
                "y": positions[n.key][1],
            }
            for n in view.nodes
        ],
        "edges": [
            {
                "number": e.number,
                "source": e.source_key,
                "target": e.target_key,
                "kind": e.edge.kind.value,
                "in_cycle": e.in_cycle,
                "to_unresolved": e.to_unresolved,
                "evidence": [
                    {"path": ev.ref.path, "line": ev.line, "rule": ev.rule}
                    for ev in e.edge.evidence
                ],
            }
            for e in view.edges
        ],
        "truncation": {**trunc.model_dump(mode="json"), "truncated": trunc.truncated},
        "summary": view.summary,
        "cycles_total": view.cycles_total,
        "limits": {"full_nodes": FULL_MAX_NODES, "full_edges": FULL_MAX_EDGES},
    }
