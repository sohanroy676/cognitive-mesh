"""Layered layout of the HTML graph (ADR-0008 amendment): fewer crossings, same content."""

import re
from pathlib import Path

from cognitive_mesh.graph import build_dependency_graph, build_view, render_html
from cognitive_mesh.graph.render_html import (
    BOX_W,
    GAP_X,
    MARGIN,
    _positions,
    crossings,
    order_layers,
)
from cognitive_mesh.graph.view import GraphView
from cognitive_mesh.ingestion import ingest_application


def reversed_pairs(root: Path, n: int = 4) -> GraphView:
    """t0 imports b{n-1}, t1 imports b{n-2}, ...: sorted order makes every pair of edges cross."""
    for i in range(n):
        (root / f"t{i}.py").write_text(f"import b{n - 1 - i}\n", encoding="utf-8")
        (root / f"b{i}.py").write_text("x = 1\n", encoding="utf-8")
    graph = build_dependency_graph(ingest_application(root))
    return build_view(graph, 300, 1000)


def test_reordering_removes_crossings_that_the_sorted_order_has(tmp_path: Path) -> None:
    view = reversed_pairs(tmp_path)
    before = crossings([list(layer) for layer in view.layers], view.edges)
    after = crossings(order_layers(view.layers, view.edges), view.edges)
    assert before == 6
    assert after == 0


def test_reordering_only_permutes_within_each_layer(tmp_path: Path) -> None:
    view = reversed_pairs(tmp_path)
    order = order_layers(view.layers, view.edges)
    assert [sorted(layer) for layer in order] == [sorted(layer) for layer in view.layers]


def test_the_layout_is_deterministic(tmp_path: Path) -> None:
    view = reversed_pairs(tmp_path)
    assert order_layers(view.layers, view.edges) == order_layers(view.layers, view.edges)
    assert render_html(view) == render_html(view)


def test_the_fixture_graph_never_gets_more_crossings() -> None:
    root = Path(__file__).parent / "fixtures" / "dep_app"
    view = build_view(build_dependency_graph(ingest_application(root)), 300, 1000)
    before = crossings([list(layer) for layer in view.layers], view.edges)
    after = crossings(order_layers(view.layers, view.edges), view.edges)
    assert after <= before


def test_short_rows_are_centered_under_the_widest_row(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text("import top\n", encoding="utf-8")
    (tmp_path / "top.py").write_text("x = 1\n", encoding="utf-8")
    view = build_view(build_dependency_graph(ingest_application(tmp_path)), 300, 1000)
    pos, width, _ = _positions(view)
    top_x = next(x for key, (x, _y) in pos.items() if key == view.layers[-1][0])
    row_xs = sorted(x for key, (x, _y) in pos.items() if key in view.layers[0])
    assert top_x == row_xs[1]  # the single node sits under the middle of three
    assert min(x for x, _ in pos.values()) >= MARGIN
    assert width >= 2 * MARGIN + 3 * BOX_W + 2 * GAP_X


def test_every_shown_node_is_still_drawn_exactly_once(tmp_path: Path) -> None:
    view = reversed_pairs(tmp_path)
    html = render_html(view)
    for vn in view.nodes:
        assert len(re.findall(f'<g class="node [^"]*" id="{vn.key}">', html)) == 1


def test_an_empty_view_still_renders() -> None:
    graph = build_dependency_graph(
        ingest_application(Path(__file__).parent / "fixtures" / "dep_app")
    )
    view = build_view(graph, 1, 1)
    assert "<svg" in render_html(view)
