"""Dependency graph data model. Direction: `A -> B` always means "A depends on / imports B"."""

from collections import Counter
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.ingestion import SourceRef


class GraphError(Exception):
    """A graph query named a node that does not exist. The only exception the graph raises."""


class NodeKind(StrEnum):
    COMPONENT = "component"  # a directory holding a supported manifest
    PACKAGE = "package"  # an external dependency declared in a manifest
    MODULE = "module"  # an ingested Python or JS/TS source file
    STDLIB = "stdlib"  # an import matching the running interpreter's stdlib module names
    UNRESOLVED = "unresolved"  # something referenced that could not be resolved; never guessed


class EdgeKind(StrEnum):
    DECLARES = "declares"  # component -> package (or unresolved requirement)
    IMPORTS = "imports"  # module -> module | stdlib | unresolved
    CONTAINS = "contains"  # component -> module, by directory only
    REQUIRES = "requires"  # locked package -> locked package, from a lockfile


class GraphDiagnosticCode(StrEnum):
    NO_MANIFEST = "no_manifest"
    UNSUPPORTED_MANIFEST = "unsupported_manifest"
    PARSE_ERROR = "parse_error"
    UNPARSEABLE_REQUIREMENT = "unparseable_requirement"
    SKIPPED_ENTRY = "skipped_entry"
    AMBIGUOUS_IMPORT = "ambiguous_import"
    TRANSITIVE_UNKNOWN = "transitive_unknown"
    COMPUTED_IMPORT = "computed_import"
    JS_RESOLUTION_LIMITED = "js_resolution_limited"


Severity = Literal["info", "warning", "error"]
Ecosystem = Literal["pypi", "npm"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Node(_Frozen):
    id: str
    kind: NodeKind
    name: str
    ecosystem: Ecosystem | None = None
    ref: SourceRef | None = None  # file that first introduced the node
    # Heuristic only: an unresolved import whose normalized name equals a declared package.
    # Import names and distribution names differ (yaml / PyYAML), so this is never an edge.
    candidate_package: str | None = None


class Evidence(_Frozen):
    """Why an edge exists: the source file, line (when locatable), rule and the cited text."""

    ref: SourceRef
    line: int | None
    rule: str
    detail: str


class Edge(_Frozen):
    source: str
    target: str
    kind: EdgeKind
    evidence: tuple[Evidence, ...] = Field(min_length=1)  # no edge without supporting input


class GraphDiagnostic(_Frozen):
    """An explicit note about something skipped or unknown. Never contains file content."""

    path: str
    code: GraphDiagnosticCode
    severity: Severity
    message: str


class Reached(_Frozen):
    """A node reached by traversal, with the shortest chain of edges that reaches it."""

    node: Node
    depth: int
    path: tuple[Edge, ...]


def count_values(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
