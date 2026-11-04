"""Compare two contract snapshots against what the other projects use (ADR-0041).

Every difference gets one of: `breaking` (a dependent uses what changed), `compatible` (an addition,
or a change the dependents still fit), `unknown` (the extractors cannot tell) or `unused` (no
dependent uses it). Nothing is called compatible without the reason; what cannot be read is
`unknown`, never `compatible`. Request and response bodies are not compared.
"""

import re
from collections.abc import Iterable, Mapping, Sequence

from cognitive_mesh.graph.builders import normalize_pypi
from cognitive_mesh.impact.models import DependentSite, Finding, Verdict
from cognitive_mesh.impact.snapshot import Snapshot, TableKey, TableShape, merge_tables
from cognitive_mesh.impact.usage import package_usage
from cognitive_mesh.impact.versions import Fit, spec_of, version_fits
from cognitive_mesh.workspace.contracts import match_label
from cognitive_mesh.workspace.exports import PackageContract
from cognitive_mesh.workspace.facts import Call, Column, Route
from cognitive_mesh.workspace.graph import ProjectRead
from cognitive_mesh.workspace.models import PackageEdge, Site

_RANK = {"exact": 0, "param_style": 1, "value_for_param": 2}
_FAMILY = re.compile(r"^[^(\[<]*")
_ADDED = "added; nothing that exists today can depend on it"
_MAX_UNKNOWN_SITES = 5
_ORDER = {Verdict.BREAKING: 0, Verdict.UNKNOWN: 1, Verdict.COMPATIBLE: 2, Verdict.UNUSED: 3}


def _dep(project: str, site: Site) -> DependentSite:
    return DependentSite(project=project, site=site)


def _unique(items: Iterable[DependentSite]) -> tuple[DependentSite, ...]:
    return tuple(
        sorted(
            dict.fromkeys(items),
            key=lambda d: (d.project, d.site.path, d.site.line or 0, d.site.detail),
        )
    )


def _finding(
    kind: str,
    verdict: Verdict,
    reason: str,
    subject: str,
    provider: Site | None,
    dependents: Iterable[DependentSite] = (),
    note: str = "",
) -> Finding:
    return Finding(
        kind=kind,
        verdict=verdict,
        reason=reason,
        subject=subject,
        provider=provider,
        dependents=_unique(dependents),
        note=note,
    )


def _best_route(routes: Iterable[Route], call: Call) -> Route | None:
    best: tuple[int, Route] | None = None
    for route in routes:
        if route.method != call.method:
            continue
        label = match_label(route.path, call.path)
        if label is not None and (best is None or _RANK[label] < best[0]):
            best = (_RANK[label], route)
    return best[1] if best else None


# --- API ---------------------------------------------------------------------------------------


def _api(base: Snapshot, now: Snapshot, others: Mapping[str, ProjectRead]) -> list[Finding]:
    base_ok = [r for r in base.routes if r.problem is None]
    now_ok = [r for r in now.routes if r.problem is None]
    now_ok_keys = {(r.method, r.path) for r in now_ok}
    now_problem = {(r.method, r.path) for r in now.routes if r.problem is not None}
    uses: dict[tuple[str, str], list[tuple[str, Call]]] = {}
    for project, read in sorted(others.items()):
        for call in read.facts.calls:
            route = _best_route(base_ok, call)
            if route is not None:
                uses.setdefault((route.method, route.path), []).append((project, call))
    computed = [
        _dep(project, u.site)
        for project, read in sorted(others.items())
        for u in read.facts.unresolved
        if u.kind == "api" and u.reason == "computed_url"
    ][:_MAX_UNKNOWN_SITES]
    out: list[Finding] = []
    for route in sorted(base_ok, key=lambda r: (r.path, r.method)):
        key = (route.method, route.path)
        subject = f"{route.method} {route.path}"
        if key in now_ok_keys:
            continue
        users = uses.get(key, [])
        if key in now_problem:
            if users:
                out.append(
                    _finding(
                        "api",
                        Verdict.UNKNOWN,
                        "route_no_longer_literal",
                        subject,
                        route.site,
                        (_dep(p, c.site) for p, c in users),
                        "the route is still declared but its path can no longer be read",
                    )
                )
            continue
        still: dict[str, list[DependentSite]] = {}
        gone: list[DependentSite] = []
        for project, call in users:
            match = _best_route(now_ok, call)
            if match is None:
                gone.append(_dep(project, call.site))
            else:
                still.setdefault(match.path, []).append(_dep(project, call.site))
        if gone:
            out.append(
                _finding(
                    "api", Verdict.BREAKING, "route_removed_or_changed", subject, route.site, gone
                )
            )
        for new_path, who in sorted(still.items()):
            out.append(
                _finding(
                    "api",
                    Verdict.COMPATIBLE,
                    "calls_still_match",
                    subject,
                    route.site,
                    who,
                    f"the same calls still match {route.method} {new_path}",
                )
            )
        if not users:
            if computed:
                out.append(
                    _finding(
                        "api",
                        Verdict.UNKNOWN,
                        "computed_callers",
                        subject,
                        route.site,
                        computed,
                        "dependents make calls whose URL is computed; they may use this route",
                    )
                )
            else:
                out.append(
                    _finding("api", Verdict.UNUSED, "route_removed_or_changed", subject, route.site)
                )
    base_keys = {(r.method, r.path) for r in base_ok}
    out.extend(
        _finding(
            "api",
            Verdict.COMPATIBLE,
            "route_added",
            f"{r.method} {r.path}",
            r.site,
            note=_ADDED,
        )
        for r in sorted(now_ok, key=lambda r: (r.path, r.method))
        if (r.method, r.path) not in base_keys
    )
    return out


# --- tables and JSON Schemas -------------------------------------------------------------------


def _users(others: Mapping[str, ProjectRead], key: TableKey) -> dict[str, TableShape]:
    found: dict[str, TableShape] = {}
    for project, read in sorted(others.items()):
        defs = [t for t in read.facts.tables if (t.kind, t.name) == key]
        if defs:
            found[project] = merge_tables(defs)[key]
    return found


def _judge(
    kind: str,
    reason: str,
    if_used: Verdict,
    subject: str,
    provider: Site | None,
    using: list[DependentSite],
    unknown: list[DependentSite],
    note: str = "",
) -> list[Finding]:
    out: list[Finding] = []
    if using:
        out.append(_finding(kind, if_used, reason, subject, provider, using, note))
    if unknown:
        out.append(
            _finding(
                kind,
                Verdict.UNKNOWN,
                "dependent_columns_unknown",
                subject,
                provider,
                unknown,
                "the dependent's columns could not be read, so use cannot be ruled out",
            )
        )
    if not out:
        out.append(_finding(kind, Verdict.UNUSED, reason, subject, provider))
    return out


def _family(type_text: str) -> str:
    match = _FAMILY.match(type_text)
    return match.group(0) if match else type_text


def _column_changes(
    kind: str,
    subject: str,
    provider: Site,
    before: Column,
    after: Column,
    using: list[DependentSite],
    unknown: list[DependentSite],
) -> list[Finding]:
    out: list[Finding] = []
    if before.type and after.type and before.type != after.type:
        detail = f"{before.type} -> {after.type}"
        if _family(before.type) != _family(after.type):
            out.extend(
                _judge(
                    kind,
                    "type_changed",
                    Verdict.BREAKING,
                    subject,
                    provider,
                    using,
                    unknown,
                    detail,
                )
            )
        else:
            out.extend(
                _judge(
                    kind,
                    "type_detail_changed",
                    Verdict.UNKNOWN,
                    subject,
                    provider,
                    using,
                    unknown,
                    detail,
                )
            )
    if (
        before.nullable is not None
        and after.nullable is not None
        and before.nullable != after.nullable
    ):
        out.extend(
            _judge(
                kind,
                "nullability_changed",
                Verdict.UNKNOWN,
                subject,
                provider,
                using,
                unknown,
                f"nullable {before.nullable} -> {after.nullable}",
            )
        )
    return out


def _tables(base: Snapshot, now: Snapshot, others: Mapping[str, ProjectRead]) -> list[Finding]:
    out: list[Finding] = []
    for key in sorted(set(base.tables) | set(now.tables)):
        kind = "table" if key[0] == "table" else "json_schema"
        label = "table" if key[0] == "table" else "schema"
        before, after = base.tables.get(key), now.tables.get(key)
        users = _users(others, key)
        everyone = [_dep(p, s.site) for p, s in users.items()]
        if before is None:
            if after is not None:
                out.append(
                    _finding(
                        kind,
                        Verdict.COMPATIBLE,
                        f"{label}_added",
                        f"{label} {key[1]}",
                        after.site,
                        note=_ADDED,
                    )
                )
            continue
        if after is None:
            verdict = Verdict.BREAKING if users else Verdict.UNUSED
            out.append(
                _finding(
                    kind, verdict, f"{label}_removed", f"{label} {key[1]}", before.site, everyone
                )
            )
            continue
        for name in sorted(set(before.columns) | set(after.columns)):
            subject = f"{label} {key[1]}.{name}"
            old, new = before.columns.get(name), after.columns.get(name)
            using = [_dep(p, s.site) for p, s in users.items() if name in s.columns]
            unknown = [_dep(p, s.site) for p, s in users.items() if not s.columns]
            if old is not None and new is None:
                out.extend(
                    _judge(
                        kind,
                        "column_removed_or_renamed",
                        Verdict.BREAKING,
                        subject,
                        before.site,
                        using,
                        unknown,
                    )
                )
            elif old is None and new is not None:
                if not users:
                    out.append(_finding(kind, Verdict.UNUSED, "column_added", subject, after.site))
                elif new.nullable is True or new.default is True:
                    out.append(
                        _finding(
                            kind,
                            Verdict.COMPATIBLE,
                            "column_added",
                            subject,
                            after.site,
                            everyone,
                            "optional or defaulted, and no dependent lists it",
                        )
                    )
                else:
                    reason = (
                        "required_column_added" if new.nullable is False else "column_added_unclear"
                    )
                    out.append(
                        _finding(kind, Verdict.UNKNOWN, reason, subject, after.site, everyone)
                    )
            elif old is not None and new is not None:
                out.extend(_column_changes(kind, subject, after.site, old, new, using, unknown))
    return out


# --- configuration names -----------------------------------------------------------------------


def _config(base: Snapshot, now: Snapshot, others: Mapping[str, ProjectRead]) -> list[Finding]:
    from cognitive_mesh.workspace.config_names import GENERIC_NAMES

    out: list[Finding] = []
    computed = [
        _dep(project, u.site)
        for project, read in sorted(others.items())
        for u in read.facts.unresolved
        if u.kind == "config" and u.reason == "computed_name"
    ][:_MAX_UNKNOWN_SITES]
    for name in sorted(set(base.env_sets) - set(now.env_sets)):
        readers = [
            _dep(project, r.site)
            for project, read in sorted(others.items())
            for r in read.facts.env_reads
            if r.name == name
        ]
        setters = sorted(
            p for p, read in others.items() if any(s.name == name for s in read.facts.env_sets)
        )
        note = f"also set by: {', '.join(setters)}" if setters else "no other project sets it"
        if name.upper() in GENERIC_NAMES:
            note += "; generic name"
        provider = base.env_sets[name]
        if readers:
            out.append(
                _finding(
                    "config", Verdict.BREAKING, "setting_removed", name, provider, readers, note
                )
            )
        elif computed:
            out.append(
                _finding(
                    "config",
                    Verdict.UNKNOWN,
                    "computed_readers",
                    name,
                    provider,
                    computed,
                    "dependents read names that are computed; they may read this one",
                )
            )
        else:
            out.append(_finding("config", Verdict.UNUSED, "setting_removed", name, provider))
    out.extend(
        _finding(
            "config", Verdict.COMPATIBLE, "setting_added", name, now.env_sets[name], note=_ADDED
        )
        for name in sorted(set(now.env_sets) - set(base.env_sets))
    )
    return out


# --- packages ----------------------------------------------------------------------------------


def _key(contract: PackageContract) -> str:
    return normalize_pypi(contract.name) if contract.ecosystem == "pypi" else contract.name


def _package_findings(
    current: str,
    before: PackageContract,
    after: PackageContract | None,
    edges: Sequence[PackageEdge],
    others: Mapping[str, ProjectRead],
) -> list[Finding]:
    subject = f"package {before.name}"
    deps = [
        e
        for e in edges
        if e.target == current
        and e.published.path == before.manifest.path
        and e.ecosystem == before.ecosystem
    ]
    everyone = [_dep(e.source, e.declared[0]) for e in deps if e.declared]
    if after is None or _key(after) != _key(before):
        verdict = Verdict.BREAKING if deps else Verdict.UNUSED
        return [
            _finding(
                "package", verdict, "package_renamed_or_removed", subject, before.manifest, everyone
            )
        ]
    out: list[Finding] = []
    if before.version != after.version:
        groups: dict[tuple[Verdict, str], list[DependentSite]] = {}
        for edge in deps:
            spec = next(
                (
                    s
                    for ref in edge.declared
                    if (s := spec_of(edge.ecosystem, ref.detail)) is not None
                ),
                None,
            )
            if after.version is None:
                key = (Verdict.UNKNOWN, "version_missing")
            elif spec is None:
                key = (Verdict.UNKNOWN, "spec_not_understood")
            else:
                fit = version_fits(edge.ecosystem, spec, after.version)
                key = {
                    Fit.INSIDE: (Verdict.COMPATIBLE, "version_within_declared_range"),
                    Fit.OUTSIDE: (Verdict.BREAKING, "version_outside_declared_range"),
                    Fit.UNKNOWN: (Verdict.UNKNOWN, "spec_not_understood"),
                }[fit]
            if edge.declared:
                groups.setdefault(key, []).append(_dep(edge.source, edge.declared[0]))
        change = f"{before.version} -> {after.version}"
        out.extend(
            _finding("package", verdict, reason, subject, after.manifest, who, change)
            for (verdict, reason), who in sorted(groups.items())
        )
        if not deps:
            out.append(
                _finding(
                    "package",
                    Verdict.UNUSED,
                    "version_changed",
                    subject,
                    after.manifest,
                    note=change,
                )
            )
    out.extend(_export_findings(before, after, deps, others))
    return out


def _export_findings(
    before: PackageContract,
    after: PackageContract,
    deps: Sequence[PackageEdge],
    others: Mapping[str, ProjectRead],
) -> list[Finding]:
    if not (before.exports_complete and after.exports_complete):
        if not deps:
            return []
        return [
            _finding(
                "export",
                Verdict.UNKNOWN,
                "exports_incomplete",
                f"exports of {before.name}",
                after.manifest,
                (_dep(e.source, e.declared[0]) for e in deps if e.declared),
                "the export list could not be read in full",
            )
        ]
    uses = {
        e.source: package_usage(e.source, others[e.source].result, e.ecosystem, before.name)
        for e in deps
        if e.source in others
    }
    out: list[Finding] = []
    for name in sorted(before.exports - after.exports):
        named = [_dep(p, s) for p, u in uses.items() if name in u.names for s in u.sites[:1]]
        whole = [
            _dep(p, s)
            for p, u in uses.items()
            if u.whole and name not in u.names
            for s in u.sites[:1]
        ]
        subject = f"export {name} of {before.name}"
        if named:
            out.append(
                _finding(
                    "export", Verdict.BREAKING, "export_removed", subject, before.manifest, named
                )
            )
        if whole:
            out.append(
                _finding(
                    "export",
                    Verdict.UNKNOWN,
                    "module_imported_whole",
                    subject,
                    before.manifest,
                    whole,
                    "the dependent imports the module as a whole; the names it uses are not known",
                )
            )
        if not named and not whole:
            out.append(
                _finding("export", Verdict.UNUSED, "export_removed", subject, before.manifest)
            )
    out.extend(
        _finding(
            "export",
            Verdict.COMPATIBLE,
            "export_added",
            f"export {name} of {before.name}",
            after.manifest,
            note=_ADDED,
        )
        for name in sorted(after.exports - before.exports)
    )
    return out


def _packages(
    current: str,
    base: Snapshot,
    now: Snapshot,
    edges: Sequence[PackageEdge],
    others: Mapping[str, ProjectRead],
) -> list[Finding]:
    now_by_manifest = {p.manifest.path: p for p in now.packages}
    out: list[Finding] = []
    for before in base.packages:
        out.extend(
            _package_findings(
                current, before, now_by_manifest.get(before.manifest.path), edges, others
            )
        )
    return out


def diff_contracts(
    current: str,
    base: Snapshot,
    now: Snapshot,
    others: Mapping[str, ProjectRead],
    edges: Sequence[PackageEdge],
) -> list[Finding]:
    """The findings between `base` and `now` for the project `current`, judged against `others`
    (the other workspace projects as they are now) and the detected package `edges`."""
    findings = [
        *_api(base, now, others),
        *_tables(base, now, others),
        *_config(base, now, others),
        *_packages(current, base, now, edges, others),
    ]
    return sorted(findings, key=lambda f: (_ORDER[f.verdict], f.kind, f.subject, f.reason))
