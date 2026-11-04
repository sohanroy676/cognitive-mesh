"""A contract snapshot: what one project provides to others, read from one tree (ADR-0041).

Routes, tables and JSON Schemas with typed columns, the configuration names the project sets, and
the packages it publishes with their versions and exports. Parse-only; values are never kept.
"""

from dataclasses import dataclass
from pathlib import Path

from cognitive_mesh.ingestion import IngestionResult, ingest_application
from cognitive_mesh.workspace.exports import PackageContract, package_contracts
from cognitive_mesh.workspace.extract import extract_project
from cognitive_mesh.workspace.facts import Column, ProjectFacts, Route, TableDef
from cognitive_mesh.workspace.models import Site

TableKey = tuple[str, str]  # (kind, name)


@dataclass(frozen=True)
class TableShape:
    """All definitions of one table (or JSON Schema) in one project, merged."""

    sites: tuple[Site, ...]
    columns: dict[str, Column]

    @property
    def site(self) -> Site:
        return self.sites[0]


@dataclass(frozen=True)
class Snapshot:
    project: str
    routes: tuple[Route, ...]
    tables: dict[TableKey, TableShape]
    env_sets: dict[str, Site]
    packages: tuple[PackageContract, ...]


def merge_tables(defs: list[TableDef]) -> dict[TableKey, TableShape]:
    """Merge definitions by (kind, name). A later definition of a column replaces an earlier one
    (migration files are read in path order, which is approximate)."""
    grouped: dict[TableKey, list[TableDef]] = {}
    for table in defs:
        grouped.setdefault((table.kind, table.name), []).append(table)
    merged: dict[TableKey, TableShape] = {}
    for key, items in grouped.items():
        columns: dict[str, Column] = {}
        for item in items:
            for name in item.columns:
                columns.setdefault(name, Column(name))
            for column in item.typed:
                columns[column.name] = column
        sites = tuple(sorted({i.site for i in items}, key=lambda s: (s.path, s.line or 0)))
        merged[key] = TableShape(sites, columns)
    return merged


def snapshot_of(
    project: str, result: IngestionResult, facts: ProjectFacts | None = None
) -> Snapshot:
    found = facts if facts is not None else extract_project(project, result)
    env_sets: dict[str, Site] = {}
    for item in found.env_sets:
        env_sets.setdefault(item.name, item.site)
    return Snapshot(
        project=project,
        routes=tuple(found.routes),
        tables=merge_tables(found.tables),
        env_sets=env_sets,
        packages=tuple(package_contracts(project, result)),
    )


def snapshot_of_tree(project: str, root: Path) -> Snapshot:
    """Ingest a folder (read-only, never executed) and snapshot it."""
    return snapshot_of(project, ingest_application(root))
