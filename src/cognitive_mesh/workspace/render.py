"""Text, JSON and Mermaid output for a `WorkspaceGraph`. Detected and declared edges never mix:
declared ones are labelled `declared by a person` in text and JSON and drawn dotted in Mermaid.
Project names, notes and manifest text are untrusted; Mermaid ids are generated."""

from typing import Any

from cognitive_mesh.graph.render_mermaid import encode
from cognitive_mesh.graph.view import escape_printable
from cognitive_mesh.workspace.graph import WorkspaceGraph
from cognitive_mesh.workspace.models import (
    ApiEdge,
    ConfigEdge,
    ManifestRef,
    PackageEdge,
    SchemaEdge,
    Unresolved,
)

_MAX_LABEL = 80


def _where(ref: ManifestRef) -> str:
    return f"{ref.project}:{ref.path}" + (f":{ref.line}" if ref.line is not None else "")


def _edge_json(edge: PackageEdge) -> dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "kind": "package",
        "ecosystem": edge.ecosystem,
        "name": edge.name,
        "origin": "detected",
        "ambiguous": edge.ambiguous,
        "candidates": list(edge.candidates),
        "declared_in": [ref.model_dump() for ref in edge.declared],
        "published_in": edge.published.model_dump(),
    }


def _api_json(e: ApiEdge) -> dict[str, Any]:
    return {
        "source": e.source,
        "target": e.target,
        "kind": "api",
        "origin": "detected",
        "method": e.method,
        "path": e.path,
        "match": e.match,
        "caveats": list(e.caveats),
        "ambiguous": e.ambiguous,
        "candidates": list(e.candidates),
        "consumer": e.consumer.model_dump(),
        "provider": e.provider.model_dump(),
    }


def _schema_json(e: SchemaEdge) -> dict[str, Any]:
    return {
        "source": e.source,
        "target": e.target,
        "kind": "shared_schema",
        "origin": "detected",
        "schema_kind": e.kind,
        "name": e.name,
        "label": "column_difference" if e.column_difference else None,
        "source_roles": list(e.source_roles),
        "target_roles": list(e.target_roles),
        "source_sites": [x.model_dump() for x in e.source_sites],
        "target_sites": [x.model_dump() for x in e.target_sites],
        "only_in_source": list(e.only_in_source),
        "only_in_target": list(e.only_in_target),
    }


def _config_json(e: ConfigEdge) -> dict[str, Any]:
    return {
        "source": e.source,
        "target": e.target,
        "kind": "config",
        "origin": "detected",
        "name": e.name,
        "generic": e.generic,
        "ambiguous": e.ambiguous,
        "candidates": list(e.candidates),
        "readers": [x.model_dump() for x in e.readers],
        "setters": [x.model_dump() for x in e.setters],
    }


def _unresolved_json(u: Unresolved) -> dict[str, Any]:
    return {"project": u.project, "kind": u.kind, "reason": u.reason, "site": u.site.model_dump()}


def to_json(graph: WorkspaceGraph) -> dict[str, Any]:
    return {
        "projects": [
            {
                "name": p.name,
                "kind": p.kind.value,
                "location": p.location,
                "commit": p.commit,
                "app_id": p.app_id,
            }
            for p in graph.projects
        ],
        "detected": [_edge_json(e) for e in graph.detected],
        "declared": [
            {
                "source": link.source,
                "target": link.target,
                "kind": link.kind,
                "note": link.note,
                "by": link.by,
                "origin": "declared by a person",
            }
            for link in graph.declared
        ],
        "api": [_api_json(e) for e in graph.api],
        "shared_schemas": [_schema_json(e) for e in graph.schemas],
        "config": [_config_json(e) for e in graph.config],
        "unresolved": [_unresolved_json(u) for u in graph.unresolved],
        "diagnostics": [d.model_dump() for d in graph.diagnostics],
    }


def _flag(candidates: tuple[str, ...], ambiguous: bool) -> str:
    return " AMBIGUOUS (candidates: " + ", ".join(candidates) + ")" if ambiguous else ""


def _api_line(e: ApiEdge) -> str:
    extra = "".join(f", {c}" for c in e.caveats)
    return (
        f"  {escape_printable(e.source)} calls {e.method} {escape_printable(e.path)} on "
        f"{escape_printable(e.target)} ({e.match}{extra}){_flag(e.candidates, e.ambiguous)}"
    )


def _schema_line(e: SchemaEdge) -> str:
    diff = " [column_difference]" if e.column_difference else ""
    return (
        f"  {escape_printable(e.source)} ({'/'.join(e.source_roles)}) and "
        f"{escape_printable(e.target)} ({'/'.join(e.target_roles)}) share "
        f"{e.kind.replace('_', ' ')} {escape_printable(e.name)}{diff}"
    )


def _config_line(e: ConfigEdge) -> str:
    return (
        f"  {escape_printable(e.source)} reads {escape_printable(e.name)}, "
        f"set by {escape_printable(e.target)}{_flag(e.candidates, e.ambiguous)}"
    )


def _contract_lines(graph: WorkspaceGraph) -> list[str]:
    out = [f"detected API contract edges: {len(graph.api)}"]
    for api in graph.api:
        out.append(_api_line(api))
        out.append(f"    consumer: {_where(api.consumer)}; provider: {_where(api.provider)}")
    out.append(f"detected shared schema edges: {len(graph.schemas)}")
    for schema in graph.schemas:
        out.append(_schema_line(schema))
        for site in (*schema.source_sites, *schema.target_sites):
            out.append(f"    {_where(site)}")
        if schema.column_difference:
            out.append(f"    only in {schema.source}: {', '.join(schema.only_in_source) or '-'}")
            out.append(f"    only in {schema.target}: {', '.join(schema.only_in_target) or '-'}")
    specific = [c for c in graph.config if not c.generic]
    generic = [c for c in graph.config if c.generic]
    out.append(f"detected config edges (specific names): {len(specific)}")
    out.extend(_config_line(c) for c in specific)
    out.append(f"detected config edges (generic names, low value): {len(generic)}")
    out.extend(_config_line(c) for c in generic)
    counts: dict[tuple[str, str], int] = {}
    for u in graph.unresolved:
        counts[(u.kind, u.reason)] = counts.get((u.kind, u.reason), 0) + 1
    out.append(f"unresolved (see `workspace deps PROJECT` or --json): {len(graph.unresolved)}")
    out.extend(f"  [{kind}] {reason}: {n}" for (kind, reason), n in sorted(counts.items()))
    return out


def to_text(graph: WorkspaceGraph) -> str:
    out = [f"projects: {len(graph.projects)}"]
    for p in graph.projects:
        pin = f" @ {p.commit[:12]}" if p.commit else ""
        out.append(f"  {escape_printable(p.name)} [{p.kind.value}]{pin}")
    out.append(f"detected package edges: {len(graph.detected)}")
    for e in graph.detected:
        flag = " AMBIGUOUS (candidates: " + ", ".join(e.candidates) + ")" if e.ambiguous else ""
        out.append(
            f"  {escape_printable(e.source)} -> {escape_printable(e.target)} "
            f"[{e.ecosystem}:{escape_printable(e.name)}]{flag}"
        )
        out.append("    declared in: " + "; ".join(_where(r) for r in e.declared))
        out.append(f"    published in: {_where(e.published)}")
    out.extend(_contract_lines(graph))
    out.append(f"declared by a person (not detected): {len(graph.declared)}")
    for link in graph.declared:
        out.append(
            f"  {escape_printable(link.source)} -> {escape_printable(link.target)} "
            f"[{escape_printable(link.kind)}] by {escape_printable(link.by)}: "
            f"{escape_printable(link.note)}"
        )
    for d in graph.diagnostics:
        out.append(f"diagnostic [{d.code}] {escape_printable(d.project or '-')}: {d.message}")
    return "\n".join(out) + "\n"


def to_mermaid(graph: WorkspaceGraph) -> str:
    keys = {p.name: f"p{i}" for i, p in enumerate(graph.projects)}
    out = [
        "%% cognitive-mesh workspace graph. Edge A --> B means A depends on B.",
        "%% Solid: detected, with evidence on both sides. Dotted: declared by a person.",
        "flowchart TD",
    ]
    if not graph.projects:
        out.append('  notice["empty workspace"]')
    for p in graph.projects:
        out.append(f'  {keys[p.name]}["{encode(p.name, _MAX_LABEL)} ({p.kind.value})"]')
    for e in graph.detected:
        label = encode(f"{e.ecosystem}:{e.name}", _MAX_LABEL) + (
            " ambiguous" if e.ambiguous else ""
        )
        out.append(f'  {keys[e.source]} -->|"{label}"| {keys[e.target]}')
    for api in graph.api:
        label = encode(f"api {api.method} {api.path} ({api.match})", _MAX_LABEL)
        out.append(f'  {keys[api.source]} -->|"{label}"| {keys[api.target]}')
    for schema in graph.schemas:
        label = encode(f"schema {schema.name}", _MAX_LABEL)
        out.append(f'  {keys[schema.source]} ---|"{label}"| {keys[schema.target]}')
    for config in graph.config:
        text = f"config {config.name}" + (" generic" if config.generic else "")
        out.append(
            f'  {keys[config.source]} -->|"{encode(text, _MAX_LABEL)}"| {keys[config.target]}'
        )
    for link in graph.declared:
        out.append(
            f'  {keys[link.source]} -.->|"declared: {encode(link.kind, _MAX_LABEL)}"| '
            f"{keys[link.target]}"
        )
    return "\n".join(out) + "\n"


def deps_text(graph: WorkspaceGraph, project: str) -> str:
    """Everything known about one project's links to the others, in plain sentences."""
    out = [f"{escape_printable(project)}: what it uses and what uses it", "calls (outgoing):"]
    for e in (e for e in graph.api if e.source == project):
        out += [_api_line(e), f"    evidence: {_where(e.consumer)}; {_where(e.provider)}"]
    out.append("is called by (incoming):")
    for e in (e for e in graph.api if e.target == project):
        out += [_api_line(e), f"    evidence: {_where(e.consumer)}; {_where(e.provider)}"]
    out.append("shared schemas:")
    for s in (s for s in graph.schemas if project in (s.source, s.target)):
        out.append(_schema_line(s))
        out.extend(f"    evidence: {_where(x)}" for x in (*s.source_sites, *s.target_sites))
    out.append("configuration names:")
    for c in (c for c in graph.config if project in (c.source, c.target)):
        out.append(_config_line(c) + (" [generic]" if c.generic else ""))
        out.append(f"    evidence: {'; '.join(_where(r) for r in (*c.readers, *c.setters))}")
    out.append("packages:")
    for p in (p for p in graph.detected if project in (p.source, p.target)):
        out.append(f"  {escape_printable(p.source)} -> {escape_printable(p.target)} [{p.name}]")
    out.append("declared by a person (not detected):")
    for link in (x for x in graph.declared if project in (x.source, x.target)):
        out.append(
            f"  {escape_printable(link.source)} -> {escape_printable(link.target)} "
            f"[{escape_printable(link.kind)}]: {escape_printable(link.note)}"
        )
    out.append("unresolved in this project:")
    for u in (u for u in graph.unresolved if u.project == project):
        out.append(f"  [{u.kind}] {u.reason}: {_where(u.site)} {escape_printable(u.site.detail)}")
    return "\n".join(out) + "\n"


def deps_json(graph: WorkspaceGraph, project: str) -> dict[str, Any]:
    full = to_json(graph)

    def mine(row: dict[str, Any]) -> bool:
        return project in (row.get("source"), row.get("target"))

    return {
        "project": project,
        "api": [r for r in full["api"] if mine(r)],
        "shared_schemas": [r for r in full["shared_schemas"] if mine(r)],
        "config": [r for r in full["config"] if mine(r)],
        "detected": [r for r in full["detected"] if mine(r)],
        "declared": [r for r in full["declared"] if mine(r)],
        "unresolved": [r for r in full["unresolved"] if r["project"] == project],
    }
