"""The workspace graph: projects, detected package edges and declared links, kept apart."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.workspace.link import (
    declared_dependencies,
    link_projects,
    published_names,
)
from cognitive_mesh.workspace.models import (
    DeclaredLink,
    PackageEdge,
    Project,
    WorkspaceDiagnostic,
)


class WorkspaceGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    projects: tuple[Project, ...]
    detected: tuple[PackageEdge, ...]
    declared: tuple[DeclaredLink, ...]
    diagnostics: tuple[WorkspaceDiagnostic, ...]


def build_workspace_graph(
    projects: list[Project],
    links: list[DeclaredLink],
    resolve_root: Callable[[Project], Path | None],
) -> WorkspaceGraph:
    """Ingest each project (read-only, never executed) and link them. A project that cannot be
    read is reported and contributes no edges; the others are still linked."""
    published = {}
    declared = {}
    diagnostics: list[WorkspaceDiagnostic] = []
    for project in projects:
        root = resolve_root(project)
        if root is None:
            diagnostics.append(
                WorkspaceDiagnostic(
                    project=project.name,
                    code="not_fetched",
                    message="the project is not on disk; run `workspace refresh`",
                )
            )
            continue
        try:
            result = ingest_application(root)
        except IngestionError:
            diagnostics.append(
                WorkspaceDiagnostic(
                    project=project.name,
                    code="ingestion_error",
                    message="the project directory cannot be read",
                )
            )
            continue
        published[project.name] = published_names(project.name, result)
        declared[project.name] = declared_dependencies(project.name, build_dependency_graph(result))
    return WorkspaceGraph(
        projects=tuple(projects),
        detected=tuple(link_projects(published, declared)),
        declared=tuple(links),
        diagnostics=tuple(diagnostics),
    )
