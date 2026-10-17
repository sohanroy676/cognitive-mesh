"""Render a `GraphView` as one self-contained HTML file with inline SVG (ADR-0008).

No script, no external resource, no link. Graph text (ids, names, evidence) is untrusted: it goes
only into text nodes (escaped) and never into element ids, classes, styles or URLs; those are
generated or constant. A Content-Security-Policy meta tag blocks scripts and loads as a second
layer. The output is ASCII with LF line endings and carries no timestamps or absolute paths.
"""

import html

from cognitive_mesh.graph.view import STYLE_LABELS, GraphView, NodeStyle, ViewEdge, escape_printable

BOX_W = 220
BOX_H = 46
GAP_X = 30
GAP_Y = 76
MARGIN = 40
PER_ROW = 10
_LABEL_CLIP = 30
_SWEEPS = 6  # alternating down/up passes of the crossing reduction

_CSS = """
body{font-family:sans-serif;margin:16px;background:#ffffff;color:#111827}
h1{font-size:1.4em}h2{font-size:1.1em;margin-top:1.6em}
table{border-collapse:collapse;font-size:.85em}
th,td{border:1px solid #9ca3af;padding:3px 8px;text-align:left;vertical-align:top}
td{word-break:break-all}
.banner{border:2px solid #b91c1c;background:#fee2e2;padding:8px;font-weight:bold}
.legend{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px}
.sw{display:inline-block;width:26px;height:14px;border:2px solid #374151;vertical-align:middle}
.line{display:inline-block;width:34px;height:0;border-top:3px solid #374151;vertical-align:middle}
.canvas{overflow-x:auto;border:1px solid #d1d5db}
.node rect{fill:#f3f4f6;stroke:#374151;stroke-width:1.5}
.node text{font-size:11px;fill:#111827}
.node .kind{font-size:9px;fill:#4b5563}
.k-component rect,.sw.k-component{fill:#dbeafe;stroke:#1e40af;stroke-width:2.5;background:#dbeafe}
.k-package rect,.sw.k-package{fill:#dcfce7;stroke:#166534;background:#dcfce7}
.k-stdlib rect,.sw.k-stdlib{fill:#ede9fe;stroke:#5b21b6;background:#ede9fe}
.k-unresolved rect{fill:#fef3c7;stroke:#b45309;stroke-width:2;stroke-dasharray:6 3}
.k-computed rect{fill:#ffedd5;stroke:#c2410c;stroke-width:2;stroke-dasharray:2 3}
.sw.k-unresolved{background:#fef3c7;border:2px dashed #b45309}
.sw.k-computed{background:#ffedd5;border:2px dotted #c2410c}
.sw.k-module{background:#f3f4f6}
.edge path{fill:none;stroke:#4b5563;stroke-width:1.4}
.edge text{font-size:10px;fill:#374151}
.edge.e-contains path{stroke:#9ca3af;stroke-width:1}
.edge.unres path{stroke-dasharray:6 4;stroke:#b45309}
.edge.cycle path{stroke:#b91c1c;stroke-width:3}
.edge.cycle text{fill:#b91c1c;font-weight:bold}
.line.unres{border-top:3px dashed #b45309}
.line.cycle{border-top:4px solid #b91c1c}
.line.contains{border-top:1px solid #9ca3af}
.cyc{color:#b91c1c;font-weight:bold}
""".strip()

_MARKERS = (
    '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#4b5563"></path></marker>'
    '<marker id="arrow-cycle" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
    'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#b91c1c"></path></marker>'
)


def esc(text: str) -> str:
    """Escape untrusted text for an HTML text node or a double-quoted attribute; ASCII output."""
    safe = html.escape(escape_printable(text), quote=True)
    return safe.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _clip(text: str) -> str:
    plain = escape_printable(text)
    return plain if len(plain) <= _LABEL_CLIP else plain[:_LABEL_CLIP] + "..."


def _fmt(value: object) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items()) or "none"
    return str(value)


def _neighbors(edges: tuple[ViewEdge, ...]) -> dict[str, list[str]]:
    near: dict[str, list[str]] = {}
    for e in edges:
        if e.source_key != e.target_key:
            near.setdefault(e.source_key, []).append(e.target_key)
            near.setdefault(e.target_key, []).append(e.source_key)
    return near


def order_layers(
    layers: tuple[tuple[str, ...], ...], edges: tuple[ViewEdge, ...]
) -> list[list[str]]:
    """Reorder nodes inside each layer to cut edge crossings (barycenter sweeps, deterministic).

    Layer membership never changes, only the order within a layer. Ties keep the incoming order.
    """
    order = [list(layer) for layer in layers]
    near = _neighbors(edges)
    index = {key: i for layer in order for i, key in enumerate(layer)}
    where = {key: n for n, layer in enumerate(order) for key in layer}
    for sweep in range(_SWEEPS):
        sequence = range(1, len(order)) if sweep % 2 == 0 else range(len(order) - 2, -1, -1)
        for n in sequence:
            ref = n - 1 if sweep % 2 == 0 else n + 1

            def center(key: str, ref: int = ref) -> float:
                pulls = [index[k] for k in near.get(key, []) if where[k] == ref]
                return sum(pulls) / len(pulls) if pulls else float(index[key])

            order[n].sort(key=lambda key: (center(key), index[key]))
            for i, key in enumerate(order[n]):
                index[key] = i
    return order


def crossings(order: list[list[str]], edges: tuple[ViewEdge, ...]) -> int:
    """Edge crossings between adjacent layers (a measure of how readable the picture is)."""
    where = {key: (n, i) for n, layer in enumerate(order) for i, key in enumerate(layer)}
    between: dict[int, list[tuple[int, int]]] = {}
    for e in edges:
        (ln, li), (rn, ri) = where[e.source_key], where[e.target_key]
        if abs(ln - rn) == 1:
            top, bottom = (li, ri) if ln < rn else (ri, li)
            between.setdefault(min(ln, rn), []).append((top, bottom))
    total = 0
    for pairs in between.values():
        for i, (a1, b1) in enumerate(pairs):
            for a2, b2 in pairs[i + 1 :]:
                if (a1 - a2) * (b1 - b2) < 0:
                    total += 1
    return total


def _positions(view: GraphView) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Top-left corner of every node box, plus canvas width and height."""
    pos: dict[str, tuple[int, int]] = {}
    chunks: list[list[str]] = []
    for layer in order_layers(view.layers, view.edges):
        for start in range(0, len(layer), PER_ROW):
            chunks.append(layer[start : start + PER_ROW])
    widest = max((len(c) for c in chunks), default=1)
    step = BOX_W + GAP_X
    for row, chunk in enumerate(chunks):
        shift = (widest - len(chunk)) * step // 2  # center short rows under the widest one
        for col, key in enumerate(chunk):
            pos[key] = (MARGIN + shift + col * step, MARGIN + row * (BOX_H + GAP_Y))
    rows = len(chunks)
    width = 2 * MARGIN + widest * BOX_W + (widest - 1) * GAP_X + 60  # room for self-loops
    height = 2 * MARGIN + max(rows, 1) * BOX_H + max(rows - 1, 0) * GAP_Y
    return pos, width, height


def _edge_path(edge: ViewEdge, pos: dict[str, tuple[int, int]]) -> tuple[str, float, float]:
    """SVG path data plus the label position (the curve's midpoint)."""
    sx, sy = pos[edge.source_key]
    tx, ty = pos[edge.target_key]
    if edge.source_key == edge.target_key:
        x0, y0 = sx + BOX_W, sy + BOX_H // 2 - 8
        y1 = sy + BOX_H // 2 + 8
        d = f"M{x0},{y0} C{x0 + 44},{y0 - 24} {x0 + 44},{y1 + 24} {x0},{y1}"
        return d, x0 + 33.0, float(sy + BOX_H // 2)
    cx_s, cx_t = sx + BOX_W // 2, tx + BOX_W // 2
    if ty > sy:  # dependency is lower: bottom of source to top of target
        p0, p3 = (cx_s, sy + BOX_H), (cx_t, ty)
        lift = (ty - (sy + BOX_H)) // 2
        p1, p2 = (p0[0], p0[1] + lift), (p3[0], p3[1] - lift)
    elif ty == sy:  # same row: arc over the boxes, top to top
        p0, p3 = (cx_s, sy), (cx_t, ty)
        arc = 55 if cx_s < cx_t else 90  # opposite directions get separate arcs
        p1, p2 = (p0[0], p0[1] - arc), (p3[0], p3[1] - arc)
    else:  # upward: top of source to bottom of target
        p0, p3 = (cx_s, sy), (cx_t, ty + BOX_H)
        lift = (sy - (ty + BOX_H)) // 2
        p1, p2 = (p0[0], p0[1] - lift), (p3[0], p3[1] + lift)
    d = f"M{p0[0]},{p0[1]} C{p1[0]},{p1[1]} {p2[0]},{p2[1]} {p3[0]},{p3[1]}"
    mid_x = (p0[0] + 3 * p1[0] + 3 * p2[0] + p3[0]) / 8
    mid_y = (p0[1] + 3 * p1[1] + 3 * p2[1] + p3[1]) / 8
    return d, mid_x, mid_y


def _edge_title(edge: ViewEdge) -> str:
    lines = [f"e{edge.number} {edge.edge.kind.value}: {edge.edge.source} -> {edge.edge.target}"]
    for ev in edge.edge.evidence:
        where = ev.ref.path + (f":{ev.line}" if ev.line is not None else "")
        lines.append(f"{where} {ev.rule}")
    return "\n".join(esc(line) for line in lines)


def _svg(view: GraphView) -> str:
    pos, width, height = _positions(view)
    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="dependency graph">',
        f"<defs>{_MARKERS}</defs>",
    ]
    for ve in view.edges:
        d, mx, my = _edge_path(ve, pos)
        classes = ["edge", f"e-{ve.edge.kind.value}"]
        if ve.to_unresolved:
            classes.append("unres")
        if ve.in_cycle:
            classes.append("cycle")
        marker = "arrow-cycle" if ve.in_cycle else "arrow"
        parts.append(
            f'<g class="{" ".join(classes)}" id="e{ve.number}"><title>{_edge_title(ve)}</title>'
            f'<path d="{d}" marker-end="url(#{marker})"></path>'
            f'<text x="{mx:.0f}" y="{my:.0f}">e{ve.number}</text></g>'
        )
    for vn in view.nodes:
        x, y = pos[vn.key]
        title = f"{STYLE_LABELS[vn.style]}: {esc(vn.node.id)}"
        kind_text = STYLE_LABELS[vn.style] + (" (in cycle)" if vn.in_cycle else "")
        parts.append(
            f'<g class="node k-{vn.style.value}" id="{vn.key}"><title>{title}</title>'
            f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6"></rect>'
            f'<text class="kind" x="{x + 8}" y="{y + 16}">{kind_text}</text>'
            f'<text x="{x + 8}" y="{y + 34}">{esc(_clip(vn.node.id))}</text></g>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _legend() -> str:
    swatches = "".join(
        f'<li><span class="sw k-{style.value}"></span> {STYLE_LABELS[style]}</li>'
        for style in NodeStyle
    )
    lines = (
        '<li><span class="line"></span> imports / declares (resolved)</li>'
        '<li><span class="line contains"></span> contains</li>'
        '<li><span class="line unres"></span> dependency on an unresolved item</li>'
        '<li><span class="line cycle"></span> edge inside a dependency cycle</li>'
    )
    return f'<ul class="legend">{swatches}{lines}</ul>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _edges_table(view: GraphView) -> str:
    rows = []
    for ve in view.edges:
        items = "".join(
            f"<li>{esc(ev.ref.path)}"
            f"{':' + str(ev.line) if ev.line is not None else ''} "
            f"{esc(ev.rule)} - {esc(ev.detail)}</li>"
            for ev in ve.edge.evidence
        )
        flags = ", ".join(
            flag for flag, on in (("cycle", ve.in_cycle), ("to unresolved", ve.to_unresolved)) if on
        )
        rows.append(
            [
                f"e{ve.number}",
                esc(ve.edge.source),
                ve.edge.kind.value,
                esc(ve.edge.target),
                flags or "-",
                f"<ul>{items}</ul>",
            ]
        )
    return _table(["#", "from (depends)", "kind", "to (dependency)", "flags", "evidence"], rows)


def render_html(view: GraphView) -> str:
    body = ["<h1>Dependency graph</h1>"]
    body.append("<p>An edge A to B means A depends on B. Hover an edge for its evidence.</p>")
    if view.truncation.truncated:
        body.append(f'<p class="banner" role="alert">{esc(view.truncation.notice())}</p>')
    if not view.nodes:
        body.append("<p>Empty graph: no nodes to show.</p>")
    body.append(_legend())
    body.append(f'<div class="canvas">{_svg(view)}</div>')
    body.append("<h2>Summary (full graph)</h2>")
    body.append(
        _table(["item", "value"], [[esc(k), esc(_fmt(v))] for k, v in view.summary.items()])
    )
    body.append("<h2>Edges and evidence</h2>")
    body.append(_edges_table(view))
    unresolved = [vn for vn in view.nodes if vn.style in (NodeStyle.UNRESOLVED, NodeStyle.COMPUTED)]
    body.append("<h2>Unresolved items (shown nodes)</h2>")
    body.append(
        _table(
            ["id", "type", "first seen in"],
            [
                [
                    esc(vn.node.id),
                    "computed import" if vn.style is NodeStyle.COMPUTED else "unresolved",
                    esc(vn.node.ref.path) if vn.node.ref else "-",
                ]
                for vn in unresolved
            ],
        )
    )
    body.append("<h2>Cycles</h2>")
    cycle_rows = [[esc(" -> ".join((*c, c[0])))] for c in view.cycles]
    body.append(_table(["cycle"], cycle_rows))
    if view.cycles_total > len(view.cycles) or view.cycles_truncated:
        body.append("<p>More cycles exist than were listed.</p>")
    body.append("<h2>Diagnostics</h2>")
    body.append(
        _table(
            ["severity", "code", "path", "message"],
            [[d.severity, d.code.value, esc(d.path), esc(d.message)] for d in view.diagnostics],
        )
    )
    if view.diagnostics_total > len(view.diagnostics):
        body.append(
            f"<p>Showing {len(view.diagnostics)} of {view.diagnostics_total} diagnostics.</p>"
        )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Dependency graph</title>\n"
        f"<style>\n{_CSS}\n</style></head>\n"
        "<body>\n" + "\n".join(body) + "\n</body></html>\n"
    )
