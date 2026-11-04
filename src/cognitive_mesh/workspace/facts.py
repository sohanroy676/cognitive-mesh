"""What one project's files state, before anything is matched across projects (ADR-0037).

Plain records only. Every record carries the `Site` it came from. Extractors never execute
anything and never keep a configuration value, only names.
"""

from dataclasses import dataclass, field

from cognitive_mesh.workspace.models import Site, Unresolved


@dataclass(frozen=True)
class Route:
    """An endpoint a project serves."""

    method: str  # upper case
    path: str
    site: Site
    problem: str | None = None  # prefix_unknown | unsupported_pattern: kept, never matched


@dataclass(frozen=True)
class Call:
    """An HTTP call a project makes, with a literal method and a usable path."""

    method: str
    path: str
    site: Site
    caveats: tuple[str, ...] = ()  # base_unknown


@dataclass(frozen=True)
class Column:
    """One column (or JSON Schema property) as far as the extractor could read it (ADR-0041).

    `None` means the source did not say, which is not the same as False.
    """

    name: str
    type: str | None = None  # lower case, spaces removed: `varchar(50)`
    nullable: bool | None = None  # JSON Schema: not required
    default: bool | None = None  # whether a default is declared


@dataclass(frozen=True)
class TableDef:
    kind: str  # table | json_schema
    name: str  # normalised key
    role: str  # defines | maps | describes
    site: Site
    columns: frozenset[str] = frozenset()
    typed: tuple[Column, ...] = ()  # same names as `columns`, with types where they were read


@dataclass(frozen=True)
class EnvRead:
    name: str | None  # None when the name is computed
    site: Site


@dataclass(frozen=True)
class EnvSet:
    name: str
    site: Site


@dataclass
class ProjectFacts:
    project: str
    routes: list[Route] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    tables: list[TableDef] = field(default_factory=list)
    env_reads: list[EnvRead] = field(default_factory=list)
    env_sets: list[EnvSet] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (code, message), no site
