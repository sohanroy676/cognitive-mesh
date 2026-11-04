"""Who depends on a target in the current project (ADR-0041).

Built from the workspace edges (the same evidence on both sides as `workspace deps`) plus the names
other projects import from the packages the current project publishes. What may depend on the
target but cannot be resolved (computed URLs, computed environment names, a whole-module import)
is listed as `unknown`, never dropped.
"""

import posixpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cognitive_mesh.impact.models import Dependent, ImpactError, ImpactReport
from cognitive_mesh.impact.usage import package_usage
from cognitive_mesh.workspace.exports import PackageContract
from cognitive_mesh.workspace.graph import ProjectRead, WorkspaceGraph
from cognitive_mesh.workspace.models import ApiEdge, Site

_FORMS = "file:PATH, route:METHOD /path, table:NAME, env:NAME, package or symbol:NAME"
_ROUTE = re.compile(r"^([A-Za-z]+)\s+(/\S*)$")
_MAX_LEN = 300
_MAX_SITES = 5


@dataclass(frozen=True)
class Target:
    kind: str  # file | route | table | env | package | symbol
    value: str

    def __str__(self) -> str:
        return self.kind if self.kind == "package" else f"{self.kind}:{self.value}"


def parse_target(text: str) -> Target:
    if len(text) > _MAX_LEN or any(ord(c) < 32 for c in text):
        raise ImpactError(f"the target is not valid; use {_FORMS}")
    if text.strip() == "package":
        return Target("package", "")
    kind, sep, value = text.partition(":")
    value = value.strip()
    if not sep or kind not in ("file", "route", "table", "env", "symbol") or not value:
        raise ImpactError(f"the target is not valid; use {_FORMS}")
    if kind == "route":
        match = _ROUTE.match(value)
        if match is None:
            raise ImpactError("a route target is written route:METHOD /path")
        return Target("route", f"{match.group(1).upper()} {match.group(2)}")
    if kind == "file":
        value = posixpath.normpath(value.replace("\\", "/")).removeprefix("./")
    return Target(kind, value)


def _sites(sites: tuple[Site, ...]) -> tuple[Site, ...]:
    return sites[:_MAX_SITES]


def _unknown(project: str, kind: str, subject: str, reason: str, site: Site) -> Dependent:
    return Dependent(
        status="unknown",
        project=project,
        kind=kind,
        subject=subject,
        label=reason,
        provider=None,
        consumers=(site,),
    )


def _computed(
    others: Mapping[str, ProjectRead], kind: str, reason: str, subject: str
) -> list[Dependent]:
    found = [
        _unknown(project, kind, subject, reason, u.site)
        for project, read in sorted(others.items())
        for u in read.facts.unresolved
        if u.kind == kind and u.reason == reason
    ]
    return found[:_MAX_SITES]


def _contract_for(read: ProjectRead, manifest_path: str, ecosystem: str) -> PackageContract | None:
    return next(
        (c for c in read.packages if c.manifest.path == manifest_path and c.ecosystem == ecosystem),
        None,
    )


def _api(current: str, wg: WorkspaceGraph, accept: Callable[[ApiEdge], bool]) -> list[Dependent]:
    return [
        Dependent(
            status="detected",
            project=e.source,
            kind="api",
            subject=f"{e.method} {e.path}",
            label=e.match,
            provider=e.provider,
            consumers=(e.consumer,),
            caveats=e.caveats,
        )
        for e in wg.api
        if e.target == current and accept(e)
    ]


def _exports(
    current: str,
    read: ProjectRead,
    others: Mapping[str, ProjectRead],
    wg: WorkspaceGraph,
    names: set[str] | None,
) -> list[Dependent]:
    out: list[Dependent] = []
    for edge in wg.detected:
        if edge.target != current or edge.source not in others:
            continue
        contract = _contract_for(read, edge.published.path, edge.ecosystem)
        if contract is None:
            continue
        use = package_usage(edge.source, others[edge.source].result, edge.ecosystem, contract.name)
        taken = use.names if names is None else use.names & names
        out.extend(
            Dependent(
                status="detected",
                project=edge.source,
                kind="export",
                subject=f"{name} of {contract.name}",
                label="imported_by_name",
                provider=contract.manifest,
                consumers=_sites(use.sites),
            )
            for name in sorted(taken)
        )
        if use.whole:
            out.append(
                Dependent(
                    status="unknown",
                    project=edge.source,
                    kind="export",
                    subject=f"the module {contract.name}",
                    label="module_imported_whole",
                    provider=contract.manifest,
                    consumers=_sites(use.sites),
                )
            )
    return out


def _package_edges(
    current: str, wg: WorkspaceGraph, only_path: str | None = None
) -> list[Dependent]:
    return [
        Dependent(
            status="detected",
            project=e.source,
            kind="package",
            subject=e.name,
            label=e.ecosystem,
            provider=e.published,
            consumers=e.declared,
        )
        for e in wg.detected
        if e.target == current and (only_path is None or e.published.path == only_path)
    ]


def _check_exists(target: Target, read: ProjectRead) -> None:
    facts = read.facts
    found = True
    if target.kind == "file":
        found = any(f.ref.path == target.value for f in read.result.files)
    elif target.kind == "route":
        found = any(f"{r.method} {r.path}" == target.value for r in facts.routes)
    elif target.kind == "table":
        found = any(t.name.lower() == target.value.lower() for t in facts.tables)
    elif target.kind == "env":
        found = any(s.name == target.value for s in facts.env_sets)
    elif target.kind == "package":
        found = bool(read.packages)
    elif target.kind == "symbol":
        found = any(target.value in c.exports for c in read.packages)
    if not found:
        raise ImpactError("the target was not found in the current project")


def list_dependents(
    current: str, target: Target, wg: WorkspaceGraph, reads: Mapping[str, ProjectRead]
) -> ImpactReport:
    """Every project and location that depends on `target` of the project `current`."""
    if current not in reads:
        raise ImpactError("the current project could not be read")
    read = reads[current]
    others = {name: r for name, r in reads.items() if name != current}
    _check_exists(target, read)
    items: list[Dependent] = []
    if target.kind == "route":
        items = _api(current, wg, lambda e: f"{e.method} {e.path}" == target.value)
        items += _computed(others, "api", "computed_url", target.value)
    elif target.kind == "table":
        for e in wg.schemas:
            if target.value.lower() != e.name.lower() or current not in (e.source, e.target):
                continue
            mine = e.source == current
            items.append(
                Dependent(
                    status="detected",
                    project=e.target if mine else e.source,
                    kind="table" if e.kind == "table" else "json_schema",
                    subject=e.name,
                    label="column_difference" if e.column_difference else "same_name",
                    provider=(e.source_sites if mine else e.target_sites)[0],
                    consumers=_sites(e.target_sites if mine else e.source_sites),
                )
            )
    elif target.kind == "env":
        items = [
            Dependent(
                status="detected",
                project=e.source,
                kind="config",
                subject=e.name,
                label="generic" if e.generic else "reads",
                provider=e.setters[0],
                consumers=_sites(e.readers),
            )
            for e in wg.config
            if e.target == current and e.name == target.value
        ]
        items += _computed(others, "config", "computed_name", target.value)
    elif target.kind == "package":
        items = _package_edges(current, wg) + _exports(current, read, others, wg, None)
    elif target.kind == "symbol":
        items = _exports(current, read, others, wg, {target.value})
    else:  # file
        items = _api(current, wg, lambda e: e.provider.path == target.value)
        items += [
            Dependent(
                status="detected",
                project=e.source,
                kind="config",
                subject=e.name,
                label="generic" if e.generic else "reads",
                provider=next(s for s in e.setters if s.path == target.value),
                consumers=_sites(e.readers),
            )
            for e in wg.config
            if e.target == current and any(s.path == target.value for s in e.setters)
        ]
        for e in wg.schemas:
            mine = e.source == current
            if current not in (e.source, e.target):
                continue
            sites = e.source_sites if mine else e.target_sites
            if any(s.path == target.value for s in sites):
                items.append(
                    Dependent(
                        status="detected",
                        project=e.target if mine else e.source,
                        kind="table" if e.kind == "table" else "json_schema",
                        subject=e.name,
                        label="column_difference" if e.column_difference else "same_name",
                        provider=next(s for s in sites if s.path == target.value),
                        consumers=_sites(e.target_sites if mine else e.source_sites),
                    )
                )
        items += _package_edges(current, wg, target.value)
        entries = {c.entry for c in read.packages if c.entry}
        if target.value in entries:
            items += _exports(current, read, others, wg, None)
    declared = tuple(link for link in wg.declared if current in (link.source, link.target))
    ordered = sorted(
        items, key=lambda d: (d.status != "detected", d.project, d.kind, d.subject, d.label)
    )
    return ImpactReport(
        project=current, target=str(target), dependents=tuple(ordered), declared=declared
    )
