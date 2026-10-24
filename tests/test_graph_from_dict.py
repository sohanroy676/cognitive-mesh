"""`DependencyGraph.from_dict`: a saved `graph --json` file becomes a graph again (ADR-0035)."""

import copy
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.graph import DependencyGraph, GraphError, build_dependency_graph
from cognitive_mesh.ingestion import ingest_application

FIXTURES = Path(__file__).parent / "fixtures"
SENTINEL = "SENTINEL-FILE-TEXT"


def saved(name: str) -> dict[str, Any]:
    graph = build_dependency_graph(ingest_application(FIXTURES / name))
    data: dict[str, Any] = graph.to_dict()
    return data


@pytest.mark.parametrize("fixture", ["dep_app", "js_app", "lock_app"])
def test_a_saved_graph_round_trips_exactly(fixture: str) -> None:
    data = saved(fixture)
    again = DependencyGraph.from_dict(copy.deepcopy(data))
    assert again.to_dict() == data
    assert len(data["nodes"]) > 3 and data["edges"]


def test_the_summary_in_a_file_is_recomputed_not_trusted() -> None:
    data = saved("dep_app")
    honest = data["summary"]
    data["summary"] = {"nodes_by_kind": {"module": 999999}, "lie": True}
    assert DependencyGraph.from_dict(data).summary() == honest
    del data["summary"]  # a file without one is fine too
    assert DependencyGraph.from_dict(data).summary() == honest


def broken(kind: str) -> object:
    data = saved("dep_app")
    if kind == "unknown_node_kind":
        data["nodes"][0]["kind"] = "bogus"
        data["nodes"][0]["name"] = SENTINEL
    elif kind == "dangling_edge":
        data["edges"][0]["target"] = f"module:{SENTINEL}"
    elif kind == "duplicate_node":
        data["nodes"].append(copy.deepcopy(data["nodes"][0]))
    elif kind == "edge_without_evidence":
        data["edges"][0]["evidence"] = []
    elif kind == "missing_nodes":
        del data["nodes"]
    elif kind == "nodes_not_a_list":
        data["nodes"] = SENTINEL
    elif kind == "bad_diagnostic":
        data["diagnostics"] = [{"code": SENTINEL}]
    elif kind == "a_list":
        return [data]
    elif kind == "a_string":
        return SENTINEL
    elif kind == "none":
        return None
    return data


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_node_kind",
        "dangling_edge",
        "duplicate_node",
        "edge_without_evidence",
        "missing_nodes",
        "nodes_not_a_list",
        "bad_diagnostic",
        "a_list",
        "a_string",
        "none",
    ],
)
def test_anything_that_is_not_a_graph_is_refused_without_echoing_its_text(kind: str) -> None:
    with pytest.raises(GraphError) as raised:
        DependencyGraph.from_dict(broken(kind))
    assert str(raised.value) == "this is not a valid saved dependency graph"
    assert SENTINEL not in str(raised.value)


def test_an_unknown_extra_field_is_ignored_and_never_kept() -> None:
    data = saved("dep_app")
    data["nodes"][0]["surprise"] = SENTINEL
    data["edges"][0]["evidence"][0]["also"] = SENTINEL
    rebuilt = DependencyGraph.from_dict(data).to_dict()
    assert SENTINEL not in str(rebuilt)
    assert rebuilt == saved("dep_app")
