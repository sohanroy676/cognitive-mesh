"""The workspace graph: projects, detected package edges and declared links, kept apart."""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.workspace.config_names import link_config
from cognitive_mesh.workspace.contracts import link_api
from cognitive_mesh.workspace.extract import extract_project
from cognitive_mesh.workspace.facts import ProjectFacts
from cognitive_mesh.workspace.link import (
    declared_dependencies,
    link_projects,
    published_names,
)
from cognitive_mesh.workspace.models import (
    ApiEdge,
    ConfigEdge,
    DeclaredLink,
    PackageEdge,
    Project,
    SchemaEdge,
    Unresolved,
    WorkspaceDiagnostic,
)
from cognitive_mesh.workspace.schemas import link_schemas


class WorkspaceGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    projects: tuple[Project, ...]
    detected: tuple[PackageEdge, ...]
    declared: tuple[DeclaredLink, ...]
    diagnostics: tuple[WorkspaceDiagnostic, ...]
    api: tuple[ApiEdge, ...] = ()
    schemas: tuple[SchemaEdge, ...] = ()
    config: tuple[ConfigEdge, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()


def build_workspace_graph(
    projects: list[Project],
    links: list[DeclaredLink],
    resolve_root: Callable[[Project], Path | None],
) -> WorkspaceGraph:
    """Ingest each project (read-only, never executed) and link them. A project that cannot be
    read is reported and contributes no edges; the others are still linked."""
    published = {}
    declared = {}
    facts: dict[str, ProjectFacts] = {}
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
        facts[project.name] = extract_project(project.name, result)
        diagnostics.extend(
            WorkspaceDiagnostic(project=project.name, code=code, message=message)
            for code, message in dict.fromkeys(facts[project.name].notes)
        )
    api, api_open = link_api(facts)
    schema_edges, schema_open = link_schemas(facts)
    config, config_open = link_config(facts)
    return WorkspaceGraph(
        projects=tuple(projects),
        detected=tuple(link_projects(published, declared)),
        declared=tuple(links),
        diagnostics=tuple(diagnostics),
        api=tuple(api),
        schemas=tuple(schema_edges),
        config=tuple(config),
        unresolved=tuple(api_open + schema_open + config_open),
    )
