"""The graph server's reads (ADR-0044): the workspace dependency graph and impact on a target.

Read-only. The workspace file is read on every request, so an edge the chat adds appears after a
reload. Detected and declared edges are never merged: every edge says which it is.
"""

from __future__ import annotations

from typing import Any

from cognitive_mesh.impact import ImpactError, list_dependents, parse_target
from cognitive_mesh.impact.render import impact_json
from cognitive_mesh.serve.config import ServeConfig
from cognitive_mesh.serve.http import ApiError, Handler, Reply, Request, need
from cognitive_mesh.workspace.cli import load_workspace
from cognitive_mesh.workspace.models import WorkspaceError
from cognitive_mesh.workspace.render import to_json

GROUPS = ("detected", "api", "shared_schemas", "config")


def graph_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Nodes and one flat edge list from `workspace.render.to_json`."""
    edges: list[dict[str, Any]] = []
    for group in GROUPS:
        for e in data.get(group, []):
            edges.append(
                {
                    "source": e["source"],
                    "target": e["target"],
                    "kind": str(e.get("kind") or group),
                    "group": group,
                    "declared": False,
                    "origin": "detected from the projects' files",
                    "detail": e,
                }
            )
    for e in data.get("declared", []):
        edges.append(
            {
                "source": e["source"],
                "target": e["target"],
                "kind": e["kind"],
                "group": "declared",
                "declared": True,
                "origin": "declared by a person; not detected evidence",
                "detail": e,
            }
        )
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    nodes = [{**p, "degree": degree.get(p["name"], 0)} for p in data["projects"]]
    return {
        "nodes": nodes,
        "edges": edges,
        "unresolved": data.get("unresolved", []),
        "diagnostics": data.get("diagnostics", []),
        "counts": {g: len(data.get(g, [])) for g in (*GROUPS, "declared")},
    }


class GraphApi:
    def __init__(self, config: ServeConfig) -> None:
        self.config = config

    def routes(self) -> dict[str, Handler]:
        return {"/api/workspace": self.workspace, "/api/workspace/impact": self.impact}

    def _load(self) -> Any:
        if not self.config.workspace.is_file():
            raise ApiError(409, "no projects yet: add them in the chat's onboarding")
        try:
            return load_workspace(self.config.workspace)
        except WorkspaceError as exc:
            raise ApiError(409, str(exc)) from None

    def workspace(self, _req: Request) -> Reply:
        graph, _ = self._load()
        return 200, graph_payload(to_json(graph))

    def impact(self, req: Request) -> Reply:
        project = need(req.params, "project", 200)
        target = need(req.params, "target", 300)
        graph, reads = self._load()
        if project not in {p.name for p in graph.projects}:
            raise ApiError(404, "no project has that name")
        if project not in reads:
            raise ApiError(409, "the project is not on disk; run `workspace refresh`")
        try:
            report = list_dependents(project, parse_target(target), graph, reads)
        except ImpactError as exc:
            raise ApiError(400, str(exc)) from None
        return 200, impact_json(report)
