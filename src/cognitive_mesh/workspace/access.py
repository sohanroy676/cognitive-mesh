"""Read-only access to the projects of a workspace for agents (ADR-0041).

Every project is read through ingestion (redacted, sensitive files never opened); a `ProjectReader`
offers file text and nothing else: no path on disk, no write. Only the current project may be
written, and `require_writable` is the one gate any future write path must pass (v1.12.0).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cognitive_mesh.workspace.graph import ProjectRead, WorkspaceGraph
from cognitive_mesh.workspace.models import WorkspaceError


class ReadOnlyProjectError(WorkspaceError):
    """A write was asked for a project other than the current one. The message names no project."""


@dataclass(frozen=True)
class ReadableFile:
    path: str
    sha256: str
    language: str | None
    content: str


class ProjectReader:
    """The files of one project as ingestion read them. Nothing here can change a project."""

    def __init__(self, read: ProjectRead) -> None:
        self._files = {f.ref.path: f for f in read.result.files}
        self.name = read.project.name

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._files))

    def file(self, path: str) -> ReadableFile | None:
        found = self._files.get(path)
        if found is None:
            return None
        return ReadableFile(found.ref.path, found.ref.sha256, found.language, found.content)


class WorkspaceAccess:
    """The workspace as agents see it: a graph, per-project readers and one writable project."""

    def __init__(
        self, current: str, graph: WorkspaceGraph, reads: Mapping[str, ProjectRead]
    ) -> None:
        if current not in reads:
            raise WorkspaceError("the current project is not a readable project of the workspace")
        self.current = current
        self.graph = graph
        self._reads: Mapping[str, ProjectRead] = MappingProxyType(dict(reads))

    @property
    def reads(self) -> Mapping[str, ProjectRead]:
        return self._reads

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._reads))

    def reader(self, name: str) -> ProjectReader:
        read = self._reads.get(name)
        if read is None:
            raise WorkspaceError("no readable project has that name")
        return ProjectReader(read)

    def require_writable(self, name: str) -> None:
        """Raise unless `name` is the current project. Other projects are read-only."""
        if name != self.current:
            raise ReadOnlyProjectError("only the current project may be changed")
