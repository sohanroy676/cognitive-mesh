"""Render a `GraphView` as Mermaid flowchart text (ADR-0008).

Graph text (ids, names, evidence) is untrusted. It never becomes a Mermaid identifier: node ids
are generated. Every character outside a small safe set is written as a Mermaid `#<code>;`
entity, so quotes, brackets, `-->`, backticks, braces (and so `%%{init}%%` directives) and
newlines cannot change the diagram's structure. The output is ASCII with LF line endings.
"""

from cognitive_mesh.graph.view import STYLE_LABELS, GraphView, NodeStyle, escape_printable

_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ._/:@,-")
_MAX_LABEL = 80

_CLASS_DEFS = {
    NodeStyle.COMPONENT: "fill:#dbeafe,stroke:#1e40af,stroke-width:2px",
    NodeStyle.MODULE: "fill:#f3f4f6,stroke:#374151",
    NodeStyle.PACKAGE: "fill:#dcfce7,stroke:#166534",
    NodeStyle.STDLIB: "fill:#ede9fe,stroke:#5b21b6",
    NodeStyle.UNRESOLVED: "fill:#fef3c7,stroke:#b45309,stroke-width:2px,stroke-dasharray:5 3",
    NodeStyle.COMPUTED: "fill:#ffedd5,stroke:#c2410c,stroke-width:2px,stroke-dasharray:2 3",
}
_NOTICE_DEF = "fill:#fee2e2,stroke:#b91c1c,stroke-width:2px"


def encode(text: str, limit: int | None = None) -> str:
    """Make untrusted text inert: escape control characters, clip, then entity-encode the rest."""
    plain = escape_printable(text)
    if limit is not None and len(plain) > limit:
        plain = plain[:limit] + "..."
    return "".join(ch if ch in _SAFE else f"#{ord(ch)};" for ch in plain)


def _evidence_summary(first_path: str, line: int | None, rule: str, extra: int) -> str:
    where = encode(first_path) + (f":{line}" if line is not None else "")
    more = f" (+{extra} more)" if extra else ""
    return f"{where} {encode(rule)}{more}"


def render_mermaid(view: GraphView) -> str:
    out = [
        "%% cognitive-mesh dependency graph. Edge A --> B means A depends on B.",
        "%% Edge numbers (e1, e2, ...) match the evidence comments below; "
        "use --format html or --json for full evidence text.",
    ]
    if view.truncation.truncated:
        out.append("%% " + encode(view.truncation.notice()))
    out.append("flowchart TD")
    if not view.nodes:
        out.append('  notice["empty graph: no nodes to show"]:::notice')
    for vn in view.nodes:
        label = f"{STYLE_LABELS[vn.style]}: {encode(vn.node.id, _MAX_LABEL)}"
        out.append(f'  {vn.key}["{label}"]:::{vn.style.value}')
    for ve in view.edges:
        text = f"e{ve.number} {ve.edge.kind.value}" + (" cycle" if ve.in_cycle else "")
        if ve.in_cycle:
            arrow = "==>"
        elif ve.to_unresolved:
            arrow = "-.->"
        else:
            arrow = "-->"
        out.append(f'  {ve.source_key} {arrow}|"{text}"| {ve.target_key}')
    if view.truncation.truncated:
        out.append(f'  notice["{encode(view.truncation.notice())}"]:::notice')
    for style, defs in _CLASS_DEFS.items():
        out.append(f"  classDef {style.value} {defs}")
    out.append(f"  classDef notice {_NOTICE_DEF}")
    for ve in view.edges:
        first = ve.edge.evidence[0]
        summary = _evidence_summary(
            first.ref.path, first.line, first.rule, len(ve.edge.evidence) - 1
        )
        out.append(f"%% e{ve.number}: {summary}")
    for cycle in view.cycles:
        out.append("%% cycle: " + " -> ".join(encode(c, _MAX_LABEL) for c in (*cycle, cycle[0])))
    if view.cycles_total > len(view.cycles) or view.cycles_truncated:
        out.append("%% more cycles exist than were listed")
    return "\n".join(out) + "\n"
