"""Text, JSON and Mermaid output for a `WorkspaceGraph`. Detected and declared edges never mix:
declared ones are labelled `declared by a person` in text and JSON and drawn dotted in Mermaid.
Project names, notes and manifest text are untrusted; Mermaid ids are generated."""

from typing import Any

from cognitive_mesh.graph.render_mermaid import encode
from cognitive_mesh.graph.view import escape_printable
from cognitive_mesh.workspace.graph import WorkspaceGraph
from cognitive_mesh.workspace.models import ManifestRef, PackageEdge

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
        "diagnostics": [d.model_dump() for d in graph.diagnostics],
    }


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
        "%% Solid: detected from manifests on both sides. Dotted: declared by a person.",
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
    for link in graph.declared:
        out.append(
            f'  {keys[link.source]} -.->|"declared: {encode(link.kind, _MAX_LABEL)}"| '
            f"{keys[link.target]}"
        )
    return "\n".join(out) + "\n"
