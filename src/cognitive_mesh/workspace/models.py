"""Workspace data model (ADR-0036). Records are immutable and carry their evidence."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class WorkspaceError(Exception):
    """The workspace file or a workspace operation cannot be used. Messages never contain
    stored text, file content or raw output of other programs."""


class ProjectKind(StrEnum):
    LOCAL = "local"
    GITHUB = "github"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Project(_Frozen):
    id: int
    name: str
    kind: ProjectKind
    location: str  # absolute directory (local) or normalised https URL (github)
    commit: str | None  # github only: the full 40-hex commit the project is pinned to
    app_id: str | None  # application identity (ADR-0021) at the time of adding
    app_id_how: str | None
    added_at: datetime
    removed_at: datetime | None = None


class DeclaredLink(_Frozen):
    """An edge a person states because the analysis cannot see it. Never detected evidence."""

    id: int
    source: str  # project name
    target: str  # project name
    kind: str
    note: str
    by: str
    created_at: datetime


class ManifestRef(_Frozen):
    """One manifest location inside one project."""

    project: str
    path: str  # relative to the project root, POSIX separators
    line: int | None
    detail: str


class PackageEdge(_Frozen):
    """`source` declares a dependency whose name `target`'s own manifest declares as its package
    name. Evidence on both sides; an import name alone never creates one."""

    source: str
    target: str
    ecosystem: str  # "pypi" | "npm"
    name: str  # normalised package name
    declared: tuple[ManifestRef, ...]  # where `source` declares the dependency
    published: ManifestRef  # where `target` declares its own name
    ambiguous: bool = False  # more than one project publishes the name: no one was chosen
    candidates: tuple[str, ...] = ()  # all publishers when ambiguous


class WorkspaceDiagnostic(_Frozen):
    project: str | None
    code: str  # not_fetched | unreadable | ingestion_error
    message: str
