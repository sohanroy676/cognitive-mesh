"""The file-level graph of a folder is read once, saved, and loaded from the saved file after."""

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from cognitive_mesh.ingestion import ingest_application
from serve_support import TOKEN, call, config_for, running

APP = Path(__file__).parent / "fixtures" / "dep_app"


def fetch(s: Any, folder: Path | str, extra: str = "") -> Any:
    return call(s.graph, "GET", f"/api/folder-graph?path={quote(str(folder))}{extra}", token=TOKEN)


def test_first_request_builds_and_saves_then_loads_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_for(tmp_path)
    with running(config) as s:
        first = fetch(s, APP)
        assert first.status == 200, first.raw
        data = first.json["data"]
        assert data["source"]["kind"] == "built" and data["nodes"]
        assert any(n["kind"] == "module" for n in data["nodes"])  # the files are in the graph
        assert list((config.home / "graph-cache").glob("*.json"))

        def refuse(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the folder was read again")

        monkeypatch.setattr("cognitive_mesh.serve.folder_graph.ingest_application", refuse)
        second = fetch(s, APP)
        assert second.json["data"]["source"]["kind"] == "saved"
        assert len(second.json["data"]["nodes"]) == len(data["nodes"])
    with running(config) as again:  # a new process loads the same saved file
        assert fetch(again, APP).json["data"]["source"]["kind"] == "saved"
        listed = call(again.graph, "GET", "/api/folder-graphs", token=TOKEN).json
        assert [row["folder"] for row in listed["saved"]] == [str(APP)]


def test_rebuild_reads_the_folder_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with running(config_for(tmp_path)) as s:
        fetch(s, APP)
        calls: list[int] = []
        from cognitive_mesh.serve import folder_graph

        real = ingest_application

        def counting(path: Path) -> Any:
            calls.append(1)
            return real(path)

        monkeypatch.setattr(folder_graph, "ingest_application", counting)
        assert fetch(s, APP, "&rebuild=1").json["data"]["source"]["kind"] == "built"
        assert calls == [1]


def test_bad_paths_and_a_damaged_saved_file(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    with running(config) as s:
        assert fetch(s, tmp_path / "nope").status == 409
        assert fetch(s, "   ").status == 400
        assert fetch(s, "\\\\server\\share").status == 400
        assert fetch(s, APP).status == 200
        for saved in (config.home / "graph-cache").glob("*[!a].json"):
            if not saved.name.endswith(".meta.json"):
                saved.write_text("{not json")
        again = fetch(s, APP)  # unreadable saved file: built again, not an error
        assert again.status == 200 and again.json["data"]["source"]["kind"] == "built"


def test_a_cut_view_is_still_possible(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        assert fetch(s, APP, "&limit=1").status == 200


def test_project_graph_is_kept_between_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running(config_for(tmp_path)) as s:
        added = call(
            s.app, "POST", "/api/projects/add", token=TOKEN, body={"source": str(APP), "name": "d"}
        )
        assert added.status == 200
        assert call(s.app, "GET", "/api/graph", token=TOKEN).json["from_cache"] is False

        def refuse(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the projects were analysed again")

        monkeypatch.setattr("cognitive_mesh.serve.ws_cache.load_workspace", refuse)
        again = call(s.app, "GET", "/api/graph", token=TOKEN).json
        assert again["from_cache"] is True and again["graph"]["nodes"]
        graph_page = call(s.graph, "GET", "/api/workspace", token=TOKEN)
        assert graph_page.status == 200 and graph_page.json["nodes"]


def test_the_picker_lists_project_folders_inside_a_stored_project(tmp_path: Path) -> None:
    from cognitive_mesh.serve.graph_api import MAX_CHILDREN, project_folders

    root = tmp_path / "all"
    (root / "api" / ".git").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "web" / "package.json").write_text("{}", encoding="utf-8")
    (root / "notes").mkdir()  # no marker: not a project
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "package.json").write_text("{}", encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "package.json").write_text("{}", encoding="utf-8")
    (root / "file.txt").write_text("x", encoding="utf-8")
    assert [c["name"] for c in project_folders(str(root))] == ["api", "web"]
    assert project_folders(str(tmp_path / "missing")) == []
    many = tmp_path / "many"
    for i in range(MAX_CHILDREN + 5):
        (many / f"p{i:03d}").mkdir(parents=True)
        (many / f"p{i:03d}" / "go.mod").write_text("", encoding="utf-8")
    assert len(project_folders(str(many))) == MAX_CHILDREN


def test_the_folder_list_endpoint_returns_children(tmp_path: Path) -> None:
    from cognitive_mesh.workspace.models import ProjectKind
    from cognitive_mesh.workspace.store import WorkspaceStore

    root = tmp_path / "all"
    (root / "api" / ".git").mkdir(parents=True)
    config = config_for(tmp_path / "home")
    config.home.mkdir(parents=True, exist_ok=True)
    with WorkspaceStore.create(config.workspace) as store:
        store.add_project("all", ProjectKind.LOCAL, str(root), commit=None)
    with running(config) as s:
        row = call(s.graph, "GET", "/api/folder-graphs", token=TOKEN).json["projects"][0]
    assert row["name"] == "all" and [c["name"] for c in row["children"]] == ["api"]
