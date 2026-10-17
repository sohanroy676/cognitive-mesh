"""Lockfile ingestion and transitive edges (ADR-0005 and ADR-0006 amendments)."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.graph import (
    DependencyGraph,
    EdgeKind,
    GraphDiagnosticCode,
    build_dependency_graph,
)
from cognitive_mesh.graph import lockfiles as lockfiles_module
from cognitive_mesh.ingestion import DiagnosticCode, ingest_application

LOCK_APP = Path(__file__).parent / "fixtures" / "lock_app"


def graph_of(root: Path) -> DependencyGraph:
    return build_dependency_graph(ingest_application(root))


def write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8", newline="\n")


def requires(graph: DependencyGraph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges(EdgeKind.REQUIRES)}


def test_fixture_lockfiles_give_transitive_edges_with_evidence() -> None:
    graph = graph_of(LOCK_APP)
    assert requires(graph) == {
        ("pkg:pypi:requests", "pkg:pypi:certifi"),
        ("pkg:pypi:requests", "pkg:pypi:charset-normalizer"),
        ("pkg:pypi:requests", "pkg:pypi:not-locked-extra"),
        ("pkg:npm:left-widget", "pkg:npm:shared-util"),
    }
    for edge in graph.edges(EdgeKind.REQUIRES):
        assert edge.evidence
        assert edge.evidence[0].ref.path in {"uv.lock", "package-lock.json"}
        assert edge.evidence[0].line is not None


def test_the_project_itself_is_not_an_external_package() -> None:
    ids = {n.id for n in graph_of(LOCK_APP).nodes()}
    assert "pkg:pypi:lock-app" not in ids


def test_traversal_reaches_transitive_packages() -> None:
    reached = graph_of(LOCK_APP).transitive_dependencies("component:.")
    depths = {r.node.id: r.depth for r in reached}
    assert depths["pkg:pypi:requests"] == 1
    assert depths["pkg:pypi:certifi"] == 2
    assert depths["pkg:npm:shared-util"] == 2


def test_integrity_hashes_are_not_treated_as_secrets() -> None:
    result = ingest_application(LOCK_APP)
    lock = next(f for f in result.files if f.ref.path == "package-lock.json")
    assert lock.redactions == ()
    assert "sha512-" in lock.content


def test_a_parsed_lockfile_ecosystem_is_no_longer_reported_unknown() -> None:
    diags = [
        d
        for d in graph_of(LOCK_APP).diagnostics
        if d.code is GraphDiagnosticCode.TRANSITIVE_UNKNOWN
    ]
    assert diags == []


def test_the_unknown_notice_names_only_ecosystems_without_a_lockfile(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\ndependencies = ["requests"]\n')
    write(tmp_path, "package.json", '{"name": "w", "dependencies": {"a": "1"}}')
    write(tmp_path, "uv.lock", 'version = 1\n\n[[package]]\nname = "requests"\nversion = "1"\n')
    notes = [
        d
        for d in graph_of(tmp_path).diagnostics
        if d.code is GraphDiagnosticCode.TRANSITIVE_UNKNOWN
    ]
    assert len(notes) == 1
    assert "npm packages" in notes[0].message
    assert "pypi" not in notes[0].message


def test_nested_node_modules_resolve_to_the_nearest_package(tmp_path: Path) -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/a": {"version": "1", "dependencies": {"b": "1"}},
            "node_modules/a/node_modules/b": {"version": "1", "dependencies": {"c": "1"}},
            "node_modules/c": {"version": "1"},
        },
    }
    write(tmp_path, "package.json", '{"name": "w"}')
    write(tmp_path, "package-lock.json", json.dumps(lock))
    assert requires(graph_of(tmp_path)) == {
        ("pkg:npm:a", "pkg:npm:b"),
        ("pkg:npm:b", "pkg:npm:c"),
    }


def test_a_dependency_missing_from_the_lock_adds_no_edge(tmp_path: Path) -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/a": {"dependencies": {}, "optionalDependencies": {"x": "1"}},
        },
    }
    write(tmp_path, "package.json", '{"name": "w"}')
    write(tmp_path, "package-lock.json", json.dumps(lock))
    assert requires(graph_of(tmp_path)) == set()


def test_version_1_lockfiles_are_reported_not_guessed(tmp_path: Path) -> None:
    write(tmp_path, "package.json", '{"name": "w", "dependencies": {"a": "1"}}')
    write(tmp_path, "package-lock.json", '{"lockfileVersion": 1, "dependencies": {"a": {}}}')
    graph = graph_of(tmp_path)
    assert requires(graph) == set()
    assert any(
        d.code is GraphDiagnosticCode.PARSE_ERROR and "lockfileVersion" in d.message
        for d in graph.diagnostics
    )
    assert any(d.code is GraphDiagnosticCode.TRANSITIVE_UNKNOWN for d in graph.diagnostics)


@pytest.mark.parametrize(
    ("name", "text"),
    [("uv.lock", "this = is [not toml"), ("package-lock.json", "{not json")],
)
def test_malformed_lockfiles_yield_a_diagnostic_and_no_edges(
    tmp_path: Path, name: str, text: str
) -> None:
    write(tmp_path, name, text)
    result = ingest_application(tmp_path)
    assert any(d.code is DiagnosticCode.MALFORMED for d in result.diagnostics)
    graph = build_dependency_graph(result)
    assert requires(graph) == set()
    assert any(d.code is GraphDiagnosticCode.PARSE_ERROR for d in graph.diagnostics)


def test_hostile_package_names_cannot_forge_other_nodes(tmp_path: Path) -> None:
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/evil": {"dependencies": {"../../etc/passwd": "1", "x\ny": "1"}},
            "node_modules/../../etc/passwd": {"version": "1"},
        },
    }
    write(tmp_path, "package.json", '{"name": "w"}')
    write(tmp_path, "package-lock.json", json.dumps(lock))
    graph = graph_of(tmp_path)
    for node in graph.nodes():
        assert node.id.startswith(("pkg:", "component:", "unresolved:"))


def test_other_lockfiles_stay_excluded(tmp_path: Path) -> None:
    write(tmp_path, "poetry.lock", "[[package]]\nname = 'a'\n")
    result = ingest_application(tmp_path)
    assert any(d.code is DiagnosticCode.EXCLUDED_GENERATED for d in result.diagnostics)
    assert not result.files


def test_a_huge_lockfile_is_refused_not_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lockfiles_module, "MAX_PACKAGES", 2)
    entries = "".join(f'[[package]]\nname = "p{i}"\nversion = "1"\n\n' for i in range(3))
    write(tmp_path, "uv.lock", "version = 1\n\n" + entries)
    graph = graph_of(tmp_path)
    assert requires(graph) == set()
    assert any("more than 2 packages" in d.message for d in graph.diagnostics)


def test_json_output_lists_requires_edges() -> None:
    payload = graph_of(LOCK_APP).to_dict()
    kinds = {e["kind"] for e in payload["edges"]}  # type: ignore[attr-defined]
    assert "requires" in kinds
