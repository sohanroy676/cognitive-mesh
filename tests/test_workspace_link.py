"""Cross-project package edges (ADR-0036): manifest evidence on both sides, nothing guessed."""

from datetime import UTC, datetime
from pathlib import Path

from cognitive_mesh.workspace import Project, ProjectKind, WorkspaceGraph, build_workspace_graph
from cognitive_mesh.workspace.models import DeclaredLink
from cognitive_mesh.workspace.render import to_json, to_mermaid, to_text
from workspace_support import FIXTURE, PROJECTS


def project(name: str, root: Path) -> Project:
    return Project(
        id=1,
        name=name,
        kind=ProjectKind.LOCAL,
        location=str(root),
        commit=None,
        app_id=None,
        app_id_how=None,
        added_at=datetime.now(UTC),
    )


def graph_of(names: tuple[str, ...] = PROJECTS, root: Path = FIXTURE) -> WorkspaceGraph:
    projects = [project(n, root / n) for n in names]
    return build_workspace_graph(projects, [], lambda p: Path(p.location))


def edges(graph: WorkspaceGraph) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.name) for e in graph.detected}


def test_pypi_edge_has_evidence_on_both_sides_and_normalises_names() -> None:
    graph = graph_of()
    edge = next(e for e in graph.detected if e.source == "web_app" and e.target == "lib_core")
    assert edge.name == "lib-core" and edge.ecosystem == "pypi" and not edge.ambiguous
    assert edge.declared[0].path == "pyproject.toml" and edge.declared[0].line == 4
    assert "Lib_Core" in edge.declared[0].detail
    assert edge.published.project == "lib_core" and edge.published.line == 2


def test_npm_edge_uses_exact_scoped_name() -> None:
    assert ("shop_ui", "ui_kit", "@acme/ui-kit") in edges(graph_of())


def test_dependency_nobody_publishes_stays_external() -> None:
    names = {e.name for e in graph_of().detected}
    assert "ghost-pkg" not in names and "requests" not in names and "left-pad" not in names


def test_import_name_without_a_declared_dependency_never_links() -> None:
    graph = graph_of()
    assert not [e for e in graph.detected if e.source == "import_only"]


def test_two_publishers_make_an_ambiguous_edge_to_each_and_choose_none() -> None:
    found = [e for e in graph_of().detected if e.source == "needs_twin"]
    assert {e.target for e in found} == {"dup_a", "dup_b"}
    assert all(e.ambiguous and e.candidates == ("dup_a", "dup_b") for e in found)


def test_a_project_is_never_linked_to_itself_by_its_own_name() -> None:
    assert not [e for e in graph_of().detected if e.source == e.target]


def test_removed_project_takes_its_edges_with_it() -> None:
    graph = graph_of(tuple(n for n in PROJECTS if n != "lib_core"))
    assert not [e for e in graph.detected if "lib_core" in (e.source, e.target)]


def test_unreadable_project_is_reported_and_the_rest_still_link() -> None:
    projects = [project(n, FIXTURE / n) for n in ("web_app", "lib_core")]
    missing = project("ghost", FIXTURE / "no_such_dir")
    graph = build_workspace_graph(
        [*projects, missing], [], lambda p: None if p.name == "ghost" else Path(p.location)
    )
    assert [d.code for d in graph.diagnostics] == ["not_fetched"]
    assert ("web_app", "lib_core", "lib-core") in edges(graph)


def test_malformed_manifest_does_not_crash_or_link(tmp_path: Path) -> None:
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "pyproject.toml").write_text("[project\nname =", encoding="utf-8")
    (tmp_path / "bad" / "package.json").write_text("{not json", encoding="utf-8")
    graph = build_workspace_graph(
        [project("bad", tmp_path / "bad")], [], lambda p: Path(p.location)
    )
    assert graph.detected == ()


def test_declared_links_stay_apart_from_detected_edges_in_every_output() -> None:
    link = DeclaredLink(
        id=1,
        source="web_app",
        target="shop_ui",
        kind="rpc",
        note="calls over a queue",
        by="me",
        created_at=datetime.now(UTC),
    )
    base = graph_of(("web_app", "shop_ui", "lib_core"))
    graph = base.model_copy(update={"declared": (link,)})
    data = to_json(graph)
    assert [d["origin"] for d in data["declared"]] == ["declared by a person"]
    assert all(e["origin"] == "detected" for e in data["detected"])
    assert "declared by a person (not detected): 1" in to_text(graph)
    mermaid = to_mermaid(graph)
    assert "-.->" in mermaid and mermaid.count("-.->") == 1


def test_mermaid_output_is_inert_for_hostile_project_names() -> None:
    hostile = 'x"]; click n0 call alert(1)\n%%{init: {}}%%'
    graph = WorkspaceGraph(
        projects=(project(hostile, FIXTURE),), detected=(), declared=(), diagnostics=()
    )
    out = to_mermaid(graph)
    assert "\n%%{" not in out and '"]; click' not in out
