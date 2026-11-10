"""The graph server's reads (ADR-0044): the workspace dependency graph and impact on a target.

Read-only. The workspace file is read on every request, so an edge the chat adds appears after a
reload. Detected and declared edges are never merged: every edge says which it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from cognitive_mesh.impact import ImpactError, list_dependents, parse_target
from cognitive_mesh.impact.render import impact_json
from cognitive_mesh.serve.config import ServeConfig
from cognitive_mesh.serve.folder_graph import FolderGraphs, folder_from_text
from cognitive_mesh.serve.http import ApiError, Handler, Reply, Request, need
from cognitive_mesh.workspace.cli import load_workspace
from cognitive_mesh.workspace.models import ProjectKind, WorkspaceError
from cognitive_mesh.workspace.render import to_json
from cognitive_mesh.workspace.store import WorkspaceStore

PROJECT_MARKERS: Final = (
    ".git",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "pom.xml",
    "go.mod",
    "Cargo.toml",
)
SKIPPED_FOLDERS: Final = frozenset({"node_modules", "venv", "__pycache__", "dist", "build"})
MAX_CHILDREN: Final = 50


def project_folders(root: str) -> list[dict[str, str]]:
    """The immediate sub-folders of a stored project that look like projects of their own (they
    hold a repository or a manifest). Hidden and dependency folders are skipped, links are not
    followed, and at most MAX_CHILDREN are returned. A folder that cannot be read gives none."""
    out: list[dict[str, str]] = []
    try:
        entries = sorted(Path(root).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for entry in entries:
        if len(out) >= MAX_CHILDREN:
            break
        name = entry.name
        if name.startswith(".") or name in SKIPPED_FOLDERS:
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if any((entry / marker).exists() for marker in PROJECT_MARKERS):
                out.append({"name": name, "folder": str(entry)})
        except OSError:
            continue
    return out


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
    def __init__(self, config: ServeConfig, project_graph: Any = None) -> None:
        self.config = config
        self.project_graph = project_graph  # a ws_cache.WorkspaceGraphCache, shared with the chat
        self.folders = FolderGraphs(config.home / "graph-cache")

    def routes(self) -> dict[str, Handler]:
        return {
            "/api/workspace": self.workspace,
            "/api/workspace/impact": self.impact,
            "/api/folder-graph": self.folder_graph,
            "/api/folder-graphs": self.folder_graphs,
        }

    def folder_graph(self, req: Request) -> Reply:
        folder = folder_from_text(need(req.params, "path", 1100))
        flag = req.params.get
        data = self.folders.get(folder, full=flag("limit") != "1", rebuild=flag("rebuild") == "1")
        return 200, {"data": data}

    def folder_graphs(self, _req: Request) -> Reply:
        projects: list[dict[str, Any]] = []
        if self.config.workspace.is_file():
            try:
                with WorkspaceStore.open_existing(self.config.workspace) as store:
                    projects = [
                        {
                            "name": p.name,
                            "folder": p.location,
                            "children": project_folders(p.location),
                        }
                        for p in store.projects()
                        if p.kind is ProjectKind.LOCAL
                    ]
            except WorkspaceError:
                projects = []
        return 200, {"saved": self.folders.saved(), "projects": projects}

    def _load(self) -> Any:
        if not self.config.workspace.is_file():
            raise ApiError(409, "no projects yet: add them in the chat's onboarding")
        try:
            return load_workspace(self.config.workspace)
        except WorkspaceError as exc:
            raise ApiError(409, str(exc)) from None

    def workspace(self, req: Request) -> Reply:
        if not self.config.workspace.is_file():  # nothing added yet: an empty graph, not an error
            return 200, graph_payload({"projects": []})
        if self.project_graph is None:
            graph, _ = self._load()
            return 200, graph_payload(to_json(graph))
        reply = self.project_graph.get(refresh=req.params.get("refresh") == "1")
        if reply["graph_error"] is not None:
            raise ApiError(409, reply["graph_error"])
        return 200, {**reply["graph"], "analysed_at": reply["analysed_at"]}

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
