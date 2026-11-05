"""Adding a folder to an existing project stores it and puts it into the graph (ADR-0044)."""

from pathlib import Path
from typing import Any

from serve_support import TOKEN, call, config_for, running


def post(s: Any, path: str, body: dict[str, Any]) -> Any:
    return call(s.app, "POST", path, token=TOKEN, body=body)


def make_folder(path: Path, package: str) -> Path:
    path.mkdir(parents=True)
    (path / "package.json").write_text(f'{{"name": "{package}", "version": "1.0.0"}}\n')
    return path


def test_empty_graph_is_not_an_error(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        r = call(s.graph, "GET", "/api/workspace", token=TOKEN)
        assert r.status == 200
        assert r.json["nodes"] == [] and r.json["edges"] == []


def test_attach_stores_the_folder_and_links_it_to_its_parent(tmp_path: Path) -> None:
    main = make_folder(tmp_path / "main", "main")
    extra = make_folder(tmp_path / "extra", "extra")
    config = config_for(tmp_path)
    with running(config) as s:
        assert post(s, "/api/projects/add", {"source": str(main), "name": "main"}).status == 200
        r = post(
            s, "/api/projects/attach", {"parent": "main", "source": str(extra), "name": "extra"}
        )
        assert r.status == 200, r.raw
        graph = call(s.graph, "GET", "/api/workspace", token=TOKEN).json
        names = {n["name"] for n in graph["nodes"]}
        assert names == {"main", "extra"}
        link = [e for e in graph["edges"] if e["declared"]]
        assert [(e["source"], e["target"], e["kind"]) for e in link] == [
            ("main", "extra", "part of")
        ]
    with running(config) as again:  # a new process sees the same projects: they are in the database
        state = call(again.app, "GET", "/api/state", token=TOKEN).json
        assert {p["name"] for p in state["projects"]} == {"main", "extra"}


def test_attach_refuses_unknown_parent_missing_folder_and_files(tmp_path: Path) -> None:
    main = make_folder(tmp_path / "main", "main")
    a_file = tmp_path / "note.txt"
    a_file.write_text("x")
    with running(config_for(tmp_path)) as s:
        post(s, "/api/projects/add", {"source": str(main), "name": "main"})
        assert (
            post(s, "/api/projects/attach", {"parent": "nope", "source": str(main)}).status == 404
        )
        gone = {"parent": "main", "source": str(tmp_path / "gone")}
        assert post(s, "/api/projects/attach", gone).status == 400
        file_body = {"parent": "main", "source": str(a_file)}
        r = post(s, "/api/projects/attach", file_body)
        assert r.status == 400 and "file" in r.json["error"]
        assert (
            post(s, "/api/projects/attach", {"parent": "main", "source": "x", "extra": 1}).status
            == 400
        )
