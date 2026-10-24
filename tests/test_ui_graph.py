"""The typed-folder dependency graph the UI draws (ADR-0032 points 1 to 4)."""

import json
import os
import shutil
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.ui import graphdata
from cognitive_mesh.ui.config import UiConfig
from ui_support import FIXTURES, World, digest, run, serving

DEP_APP = FIXTURES / "dep_app"


def graph_url(path: str | Path, **extra: str) -> str:
    return "/api/graph?" + urllib.parse.urlencode({"path": str(path), **extra})


def config() -> UiConfig:
    return UiConfig(port=0)


def many_modules(tmp_path: Path, count: int) -> Path:
    folder = tmp_path / "big"
    folder.mkdir()
    for i in range(count):
        (folder / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def test_a_typed_folder_builds_a_laid_out_graph_with_evidence_but_no_file_text() -> None:
    with serving(config()) as c:
        reply = c.get(graph_url(DEP_APP))
        assert reply.status == 200
        d = reply.json()["data"]
    ids = {n["id"] for n in d["nodes"]}
    assert "module:src/dep_app/a.py" in ids and "component:." in ids
    assert all(isinstance(n["x"], int) and isinstance(n["y"], int) for n in d["nodes"])
    assert d["width"] > 0 and d["height"] > 0 and d["box"]["w"] > 0
    edge = next(e for e in d["edges"] if e["kind"] == "imports")
    assert set(edge["evidence"][0]) == {"path", "line", "rule"}
    text = json.dumps(d)
    assert "sha256" not in text and "size_bytes" not in text and '"detail"' not in text
    assert d["folder"] == os.path.abspath(DEP_APP)


def test_the_full_graph_equals_the_cli_json(capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert run("graph", DEP_APP, "--json") == 0
    cli_graph = json.loads(capsys.readouterr().out)
    with serving(config()) as c:
        d = c.get(graph_url(DEP_APP, full="1")).json()["data"]
    assert {n["id"] for n in d["nodes"]} == {n["id"] for n in cli_graph["nodes"]}
    assert len(d["edges"]) == len(cli_graph["edges"])
    assert d["full"] is True and d["truncation"]["truncated"] is False


def test_the_default_view_is_truncated_visibly_and_full_removes_the_limits(
    tmp_path: Path,
) -> None:
    folder = many_modules(tmp_path, graphdata.HTML_MAX_NODES + 30)
    with serving(config()) as c:
        short = c.get(graph_url(folder)).json()["data"]
        full = c.get(graph_url(folder, full="1")).json()["data"]
    assert short["truncation"]["truncated"] is True
    assert short["truncation"]["shown_nodes"] == graphdata.HTML_MAX_NODES
    assert short["truncation"]["total_nodes"] == graphdata.HTML_MAX_NODES + 30
    assert len(full["nodes"]) == graphdata.HTML_MAX_NODES + 30
    assert full["truncation"]["truncated"] is False


def test_above_the_ceiling_the_full_graph_is_refused_with_the_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graphdata, "FULL_MAX_NODES", 5)
    with serving(config()) as c:
        reply = c.get(graph_url(DEP_APP, full="1"))
        assert reply.status == 409
        message = str(reply.json()["error"])
        assert "limit is 5 nodes" in message and "graph DIR --json" in message
        assert c.get(graph_url(DEP_APP)).status == 200  # the truncated view still works


@pytest.mark.parametrize(
    ("typed", "status", "text"),
    [
        ("", 400, "enter a folder path"),
        ("   ", 400, "enter a folder path"),
        ("a" * 1025, 400, "longer than"),
        ("C:\\x\x00y", 400, "control characters"),
        ("C:\\x\ny", 400, "control characters"),
        ("\\\\server\\share\\x", 400, "network and device"),
        ("//server/share/x", 400, "network and device"),
        ("\\\\?\\C:\\Windows", 400, "network and device"),
        ("\\\\.\\PhysicalDrive0", 400, "network and device"),
        ("this-folder-does-not-exist-anywhere", 409, "does not exist"),
    ],
)
def test_unusable_paths_are_refused_with_fixed_messages(typed: str, status: int, text: str) -> None:
    with serving(config()) as c:
        reply = c.get(graph_url(typed))
        assert reply.status == status, reply.body
        assert text in str(reply.json()["error"])


def test_a_file_that_is_not_a_saved_graph_is_refused_with_a_plain_message() -> None:
    with serving(config()) as c:
        reply = c.get(graph_url(DEP_APP / "pyproject.toml"))
        assert reply.status == 409
        assert "not a folder or a saved graph file" in str(reply.json()["error"])


def test_an_empty_path_uses_the_root_when_one_was_given() -> None:
    with serving(UiConfig(port=0, root=DEP_APP)) as c:
        reply = c.get("/api/graph?path=")
        assert reply.status == 200
        assert reply.json()["data"]["folder"] == os.path.abspath(DEP_APP)


def test_a_relative_path_resolves_against_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.copytree(DEP_APP, tmp_path / "app")
    monkeypatch.chdir(tmp_path)
    with serving(config()) as c:
        d = c.get(graph_url("app")).json()["data"]
    assert Path(d["folder"]) == tmp_path / "app"


def test_a_symlinked_root_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "link"
    try:
        link.symlink_to(DEP_APP, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    with serving(config()) as c:
        reply = c.get(graph_url(link))
        assert reply.status == 409 and "symlink or junction" in str(reply.json()["error"])


def test_odd_file_names_come_back_as_inert_text(tmp_path: Path) -> None:
    folder = tmp_path / "odd"
    folder.mkdir()
    name = "a&b'c.py"
    try:
        (folder / name).write_text("x = 1\n", encoding="utf-8")
    except OSError:
        pytest.skip("this file system rejects the name")
    with serving(config()) as c:
        reply = c.get(graph_url(folder))
        assert reply.headers["content-type"] == "application/json"
        assert any(n["id"] == f"module:{name}" for n in reply.json()["data"]["nodes"])


def test_building_a_graph_changes_nothing_in_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    shutil.copytree(DEP_APP, folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    stamps = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in files}
    before = digest(files)
    listing = sorted(p.name for p in folder.rglob("*"))
    with serving(config()) as c:
        assert c.get(graph_url(folder)).status == 200
        assert c.get(graph_url(folder, full="1")).status == 200
    assert digest(files) == before
    assert {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in files} == stamps
    assert sorted(p.name for p in folder.rglob("*")) == listing


def test_two_builds_never_run_at_the_same_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from cognitive_mesh.ingestion import ingest_application as real

    active = 0
    peak = 0
    guard = threading.Lock()

    def slow(path: Path) -> Any:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.25)
        try:
            return real(path)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr("cognitive_mesh.ui.api.ingest_application", slow)
    results: list[int] = []
    with serving(config()) as c:
        threads = [
            threading.Thread(target=lambda: results.append(c.get(graph_url(DEP_APP)).status))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    assert results == [200, 200, 200] and peak == 1


def test_the_graph_view_needs_no_stores(ui_world: World) -> None:
    with serving(UiConfig(port=0)) as c:  # no db, graph, index or root at all
        assert c.get(graph_url(ui_world.app)).status == 200
