import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from cognitive_mesh.graph import (
    DependencyGraph,
    Edge,
    EdgeKind,
    Evidence,
    GraphDiagnostic,
    GraphDiagnosticCode,
    GraphView,
    Node,
    NodeKind,
    build_dependency_graph,
    build_view,
    render_html,
    render_mermaid,
)
from cognitive_mesh.graph.render_html import esc
from cognitive_mesh.graph.render_mermaid import encode
from cognitive_mesh.ingestion import SourceRef, ingest_application

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected"

HOSTILE = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "a&b'c\"d",
    "line1\nline2\r\n\tend\x00",
    '%%{init: {"securityLevel":"loose"}}%%',
    "`tick` ]] --> x[ y{z}",
    "javascript:alert(1)",
    "‮evil",
    "lone\ud800surrogate",
    "café ☃ \U0001f600",
    "x" * 10_000,
]

ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "h1", "h2", "p", "ul", "li", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td",
    "svg", "defs", "marker", "path", "g", "rect", "text",
}  # fmt: skip
ALLOWED_ATTRS = {
    "lang", "charset", "http-equiv", "content", "name", "class", "id", "role", "aria-label",
    "width", "height", "viewbox", "x", "y", "rx", "d", "marker-end", "refx", "refy",
    "markerwidth", "markerheight", "orient", "fill",
}  # fmt: skip


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend(attrs)


def audit_html(text: str) -> _Audit:
    """Parse with the stdlib and assert only inert, whitelisted structure came out."""
    assert text.isascii()
    assert "\r" not in text
    assert "<script" not in text.lower()
    parser = _Audit()
    parser.feed(text)
    parser.close()
    assert set(parser.tags) <= ALLOWED_TAGS, set(parser.tags) - ALLOWED_TAGS
    assert {n for n, _ in parser.attrs} <= ALLOWED_ATTRS, {n for n, _ in parser.attrs}
    for name, value in parser.attrs:
        if name == "marker-end":
            assert value is not None and re.fullmatch(r"url\(#arrow(-cycle)?\)", value)
        if name in ("id", "class", "d", "x", "y"):
            assert value is not None and re.fullmatch(r"[A-Za-z0-9 ,.\-]*", value), value
    assert re.findall(r"url\((?!#)", text) == []
    assert "@import" not in text
    return parser


def make_graph(
    nodes: list[tuple[str, NodeKind]],
    edges: list[tuple[str, str, EdgeKind]],
    text: str = "ev",
    diagnostics: list[GraphDiagnostic] | None = None,
) -> DependencyGraph:
    ref = SourceRef(root="/abs/secret/root", path=text, size_bytes=1, sha256="0" * 64)
    evidence = (Evidence(ref=ref, line=7, rule=text, detail=text),)
    return DependencyGraph(
        [Node(id=i, kind=k, name=i, ref=ref) for i, k in nodes],
        [Edge(source=s, target=t, kind=k, evidence=evidence) for s, t, k in edges],
        diagnostics or [],
    )


def hostile_graph() -> DependencyGraph:
    kinds = [NodeKind.MODULE, NodeKind.UNRESOLVED, NodeKind.PACKAGE, NodeKind.STDLIB]
    nodes = [(h, kinds[i % 4]) for i, h in enumerate(HOSTILE)]
    edges = [(h, HOSTILE[(i + 1) % len(HOSTILE)], EdgeKind.IMPORTS) for i, h in enumerate(HOSTILE)]
    diag = GraphDiagnostic(
        path=HOSTILE[0],
        code=GraphDiagnosticCode.PARSE_ERROR,
        severity="warning",
        message=HOSTILE[1],
    )
    return make_graph(nodes, edges, text=HOSTILE[0], diagnostics=[diag])


def fixture_view(name: str, nodes: int = 300, edges: int = 1000) -> GraphView:
    graph = build_dependency_graph(ingest_application(FIXTURES / name))
    return build_view(graph, nodes, edges)


# --- hostile text -------------------------------------------------------------------------------


def test_html_hostile_text_cannot_inject_markup() -> None:
    page = render_html(build_view(hostile_graph(), 100, 100))
    audit_html(page)
    assert "<img" not in page
    assert "&lt;script&gt;" in page  # shown as text, not parsed
    assert "&quot;&gt;&lt;img" in page
    assert "\x00" not in page and "‮" not in page
    assert "\\u202e" in page  # visible escape instead of the bidi override
    assert "\\ud800" in page
    assert "/abs/secret/root" not in page  # absolute roots are never rendered


def test_html_long_name_is_clipped_in_the_drawing_but_complete_in_tables() -> None:
    view = build_view(hostile_graph(), 100, 100)
    page = render_html(view)
    assert ("x" * 31) not in re.sub(r"<td>.*?</td>|<title>.*?</title>", "", page, flags=re.S)
    assert "x" * 10_000 in page  # full text stays reachable in the table / tooltip


def test_mermaid_hostile_text_is_inert() -> None:
    text = render_mermaid(build_view(hostile_graph(), 100, 100))
    assert text.isascii() and "\r" not in text and "\x00" not in text
    safe_label = re.compile(r"[A-Za-z0-9 ._/:@,#;()+=-]*")
    node_line = re.compile(r'  n\d+\["([^"]*)"\]:::(\w+)')
    edge_line = re.compile(r'  n\d+ (==>|-\.->|-->)\|"([^"]*)"\| n\d+')
    for line in text.splitlines():
        if line.startswith("%%"):
            assert not set(line) & set('{}`<"'), line
            assert "%%" not in line[2:], line
        elif line.startswith("flowchart") or line.startswith("  classDef "):
            continue
        elif m := node_line.fullmatch(line):
            assert safe_label.fullmatch(m.group(1)), line
        elif m := edge_line.fullmatch(line):
            assert safe_label.fullmatch(m.group(2)), line
        elif line.startswith("  notice["):
            continue
        else:
            pytest.fail(f"unexpected line: {line!r}")
    assert "%%{" not in text  # no Mermaid directive survives
    assert text.count("\n  n") >= len(HOSTILE)


def test_escape_helpers() -> None:
    assert esc('<a href="x">&\n') == "&lt;a href=&quot;x&quot;&gt;&amp;\\n"
    assert esc("é") == "&#233;"
    assert encode("a\nb") == "a#92;nb"
    assert encode("%%{x}") == "#37;#37;#123;x#125;"
    assert encode("x" * 100, 10) == "x" * 10 + "..."


# --- fixtures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["dep_app", "js_app"])
def test_mermaid_matches_golden_file(name: str) -> None:
    got = render_mermaid(fixture_view(name, 100, 200))
    expected = (EXPECTED / f"{name}.mmd").read_text(encoding="utf-8")
    assert got == expected


@pytest.mark.parametrize("name", ["dep_app", "js_app"])
def test_html_shows_every_node_edge_and_is_inert(name: str) -> None:
    view = fixture_view(name)
    page = render_html(view)
    audit_html(page)
    for vn in view.nodes:
        assert esc(vn.node.id) in page
        assert f'id="{vn.key}"' in page
    for ve in view.edges:
        assert f'id="e{ve.number}"' in page
        assert esc(ve.edge.evidence[0].ref.path) in page
    assert re.search(r"<g class=\"edge [^\"]*unres[^\"]*\"", page)


def test_html_marks_cycle_edges_and_computed_imports() -> None:
    dep = fixture_view("dep_app")
    page = render_html(dep)
    cycle_ids = set(re.findall(r'<g class="edge[^"]*\bcycle\b[^"]*" id="(e\d+)"', page))
    assert cycle_ids == {f"e{ve.number}" for ve in dep.edges if ve.in_cycle}
    assert len(cycle_ids) == 2
    js = render_html(fixture_view("js_app"))
    assert 'class="node k-computed"' in js
    assert "computed import" in js
    assert 'class="node k-unresolved"' in js


def test_rendering_is_deterministic() -> None:
    for name in ("dep_app", "js_app"):
        assert render_html(fixture_view(name)) == render_html(fixture_view(name))
        assert render_mermaid(fixture_view(name)) == render_mermaid(fixture_view(name))


# --- edge cases ---------------------------------------------------------------------------------


def test_empty_graph_renders_explicitly() -> None:
    view = build_view(make_graph([], []), 10, 10)
    page = render_html(view)
    audit_html(page)
    assert "Empty graph" in page
    mermaid = render_mermaid(view)
    assert "empty graph" in mermaid and mermaid.startswith("%%")
    assert "flowchart TD" in mermaid


def test_single_node_graph() -> None:
    view = build_view(make_graph([("only", NodeKind.MODULE)], []), 10, 10)
    page = render_html(view)
    audit_html(page)
    assert 'id="n0"' in page and "only" in page
    assert 'n0["module: only"]:::module' in render_mermaid(view)


def test_self_import_and_two_cycle_are_drawn_as_cycles() -> None:
    graph = make_graph(
        [("a", NodeKind.MODULE), ("b", NodeKind.MODULE), ("c", NodeKind.MODULE)],
        [
            ("a", "a", EdgeKind.IMPORTS),
            ("b", "c", EdgeKind.IMPORTS),
            ("c", "b", EdgeKind.IMPORTS),
        ],
    )
    view = build_view(graph, 10, 10)
    assert all(ve.in_cycle for ve in view.edges)
    page = render_html(view)
    audit_html(page)
    assert page.count('edge e-imports cycle"') == 3
    mermaid = render_mermaid(view)
    assert 'n0 ==>|"e1 imports cycle"| n0' in mermaid
    assert mermaid.count("==>") == 3


def test_truncation_notice_is_visible_in_both_formats() -> None:
    names = [f"m{i}" for i in range(30)]
    graph = make_graph(
        [(n, NodeKind.MODULE) for n in names],
        [(names[i], names[i + 1], EdgeKind.IMPORTS) for i in range(29)],
    )
    view = build_view(graph, 5, 100)
    page = render_html(view)
    assert 'class="banner"' in page and "showing 5 of 30 nodes" in page
    audit_html(page)
    mermaid = render_mermaid(view)
    assert "showing 5 of 30 nodes" in mermaid.replace("#61;", "=")
    assert 'notice["TRUNCATED' in mermaid
    full = render_html(build_view(graph, 100, 100))
    assert 'class="banner"' not in full  # no notice when nothing was omitted


def test_large_graph_renders_within_limits() -> None:
    names = [f"m{i:04d}" for i in range(1200)]
    graph = make_graph(
        [(n, NodeKind.MODULE) for n in names],
        [(names[i], names[i + 1], EdgeKind.IMPORTS) for i in range(1199)],
    )
    page = render_html(build_view(graph, 300, 1000))
    audit_html(page)
    assert page.count('<g class="node') == 300
    assert "showing 300 of 1200 nodes" in page
