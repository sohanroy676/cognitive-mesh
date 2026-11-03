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


Site = ManifestRef  # a location inside one project; the same shape serves every edge kind


class ApiEdge(_Frozen):
    """`source` calls an endpoint that `target` serves (ADR-0037). Method equal, path aligned;
    `match` says how exactly. The host is never used."""

    source: str
    target: str
    method: str
    path: str  # the provider's path
    match: str  # exact | param_style | value_for_param
    caveats: tuple[str, ...] = ()  # e.g. base_unknown
    consumer: Site
    provider: Site
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()


class SchemaEdge(_Frozen):
    """`source` and `target` use the same table (or JSON Schema `$id`). Symmetric: `source` sorts
    before `target`. A column difference is shown, never judged."""

    source: str
    target: str
    kind: str  # table | json_schema
    name: str
    source_sites: tuple[Site, ...]
    target_sites: tuple[Site, ...]
    source_roles: tuple[str, ...]  # defines | maps | describes
    target_roles: tuple[str, ...]
    only_in_source: tuple[str, ...] = ()
    only_in_target: tuple[str, ...] = ()

    @property
    def column_difference(self) -> bool:
        return bool(self.only_in_source or self.only_in_target)


class ConfigEdge(_Frozen):
    """`source` reads an environment variable name that `target` sets (names only, never values)."""

    source: str
    target: str
    name: str
    generic: bool
    readers: tuple[Site, ...]
    setters: tuple[Site, ...]
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()


class Unresolved(_Frozen):
    """Something seen in a project that matched nothing. Listed, never dropped."""

    project: str
    kind: str  # api | schema | config
    reason: str
    site: Site


class WorkspaceDiagnostic(_Frozen):
    project: str | None
    code: str  # not_fetched | unreadable | ingestion_error
    message: str
