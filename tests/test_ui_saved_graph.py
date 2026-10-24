"""The Graph tab loads a saved graph file instead of building a folder (ADR-0035)."""

import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.ui import savedgraph
from cognitive_mesh.ui.config import UiConfig
from ui_support import FIXTURES, digest, serving

DEP_APP = FIXTURES / "dep_app"
REFERENCE = Path(__file__).parent.parent / "data" / "reference-graphs" / "projects-graph.json"
SENTINEL = "SENTINEL-FILE-TEXT"
NOT_A_GRAPH = "that file is not a saved dependency graph"


def graph_url(path: str | Path, **extra: str) -> str:
    return "/api/graph?" + urllib.parse.urlencode({"path": str(path), **extra})


def saved_dict(name: str = "dep_app") -> dict[str, Any]:
    data: dict[str, Any] = build_dependency_graph(ingest_application(FIXTURES / name)).to_dict()
    return data


def write(tmp_path: Path, data: object, name: str = "graph.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def serve() -> Any:
    return serving(UiConfig(port=0))


def test_a_saved_file_gives_the_same_graph_as_building_the_folder(tmp_path: Path) -> None:
    file = write(tmp_path, saved_dict())
    with serve() as c:
        built = c.get(graph_url(DEP_APP)).json()["data"]
        loaded = c.get(graph_url(file)).json()["data"]
    for key in ("nodes", "edges", "width", "height", "box", "truncation", "cycles_total", "limits"):
        assert loaded[key] == built[key], key
    assert loaded["nodes"] and loaded["edges"]


def test_the_page_is_told_where_the_graph_came_from(tmp_path: Path) -> None:
    file = write(tmp_path, saved_dict())
    with serve() as c:
        folder = c.get(graph_url(DEP_APP)).json()["data"]
        loaded = c.get(graph_url(file)).json()["data"]
    assert folder["source"] == {"kind": "folder"}
    source = loaded["source"]
    assert source["kind"] == "saved file" and source["path"] == os.path.abspath(file)
    assert source["bytes"] == file.stat().st_size and source["modified"].endswith("+00:00")
    assert loaded["folder"] == os.path.abspath(file)  # pins record the file as their source


def test_the_committed_reference_graph_loads_and_can_be_shown_in_full() -> None:
    assert REFERENCE.is_file()
    with serve() as c:
        short = c.get(graph_url(REFERENCE)).json()["data"]
        full = c.get(graph_url(REFERENCE, full="1")).json()["data"]
    assert short["truncation"]["total_nodes"] == 557 and short["truncation"]["total_edges"] == 1855
    assert short["truncation"]["truncated"] and len(short["nodes"]) == 300
    assert len(full["nodes"]) == 557 and not full["truncation"]["truncated"]
    assert short["source"]["kind"] == "saved file"


def corrupt(kind: str) -> object:
    data = saved_dict()
    if kind == "unknown_node_kind":
        data["nodes"][0]["kind"] = "bogus"
        data["nodes"][0]["name"] = SENTINEL
    elif kind == "dangling_edge":
        data["edges"][0]["target"] = f"module:{SENTINEL}"
    elif kind == "other_shape":
        return {"nodes": SENTINEL, "edges": [], "diagnostics": []}
    return data


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(f"this is not json {SENTINEL}", id="text"),
        pytest.param("", id="empty"),
        pytest.param("[1, 2, 3]", id="a-list"),
        pytest.param('{"unrelated": "' + SENTINEL + '"}', id="other-object"),
        pytest.param("[" * 200000, id="deep-nesting"),
        pytest.param("\x00\x01\x02" + SENTINEL, id="control-characters"),
    ],
)
def test_a_file_that_is_not_a_graph_gets_the_fixed_message_and_no_file_text(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.status == 409 and reply.json() == {"error": NOT_A_GRAPH}
    assert SENTINEL.encode() not in reply.body


@pytest.mark.parametrize("kind", ["unknown_node_kind", "dangling_edge", "other_shape"])
def test_a_json_file_of_the_wrong_shape_is_refused_the_same_way(tmp_path: Path, kind: str) -> None:
    path = write(tmp_path, corrupt(kind))
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.status == 409 and reply.json() == {"error": NOT_A_GRAPH}
    assert SENTINEL.encode() not in reply.body


def test_only_json_files_are_loaded(tmp_path: Path) -> None:
    txt = tmp_path / "graph.txt"
    txt.write_text(json.dumps(saved_dict()), encoding="utf-8")
    with serve() as c:
        reply = c.get(graph_url(txt))
        assert reply.status == 409 and "not a folder or a saved graph file" in reply.json()["error"]
        upper = write(tmp_path, saved_dict(), "GRAPH.JSON")
        assert c.get(graph_url(upper)).status == 200  # the extension is not case sensitive


def test_a_file_over_the_size_limit_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(savedgraph, "MAX_BYTES", 2 * 1024 * 1024)
    path = tmp_path / "big.json"
    path.write_text("x" * (3 * 1024 * 1024), encoding="utf-8")
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.status == 409 and "larger than 2 MB" in reply.json()["error"]


def test_an_unreadable_file_gets_the_same_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path, saved_dict())

    def deny(self: Path) -> bytes:
        raise PermissionError(f"denied {SENTINEL}")

    monkeypatch.setattr(Path, "read_bytes", deny)
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.status == 409 and reply.json() == {"error": NOT_A_GRAPH}
    assert SENTINEL.encode() not in reply.body


def test_a_symlinked_file_is_refused(tmp_path: Path) -> None:
    target = write(tmp_path, saved_dict(), "real.json")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    with serve() as c:
        reply = c.get(graph_url(link))
    assert reply.status == 409 and reply.json() == {"error": NOT_A_GRAPH}


def test_a_folder_named_like_a_json_file_is_still_a_folder(tmp_path: Path) -> None:
    folder = tmp_path / "x.json"
    folder.mkdir()
    (folder / "a.py").write_text("x = 1\n", encoding="utf-8")
    with serve() as c:
        reply = c.get(graph_url(folder))
    assert reply.status == 200 and reply.json()["data"]["source"] == {"kind": "folder"}


def test_text_cited_in_the_files_evidence_never_reaches_the_page(tmp_path: Path) -> None:
    data = saved_dict()
    for edge in data["edges"]:
        for ev in edge["evidence"]:
            ev["detail"] = f"import {SENTINEL}"
    path = write(tmp_path, data)
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.status == 200 and SENTINEL.encode() not in reply.body
    assert set(reply.json()["data"]["edges"][0]["evidence"][0]) == {"path", "line", "rule"}


def test_names_in_a_saved_file_come_back_as_inert_text(tmp_path: Path) -> None:
    evil = "<script>alert('x')</script><img src=x onerror=alert(2)>"
    data = saved_dict()
    data["nodes"][0]["name"] = evil
    path = write(tmp_path, data)
    with serve() as c:
        reply = c.get(graph_url(path))
    assert reply.headers["content-type"] == "application/json"
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert any(n["label"] == evil for n in reply.json()["data"]["nodes"])


def test_loading_a_file_changes_nothing(tmp_path: Path) -> None:
    path = write(tmp_path, saved_dict())
    before = digest([path])
    stamp = path.stat().st_mtime_ns
    with serve() as c:
        assert c.get(graph_url(path)).status == 200
        assert c.get(graph_url(path, full="1")).status == 200
    assert digest([path]) == before and path.stat().st_mtime_ns == stamp
    assert sorted(p.name for p in tmp_path.iterdir()) == ["graph.json"]


def test_a_path_that_does_not_exist_is_still_explained() -> None:
    with serve() as c:
        reply = c.get(graph_url("no-such-folder-or-file.json"))
    assert reply.status == 409 and "does not exist" in reply.json()["error"]
