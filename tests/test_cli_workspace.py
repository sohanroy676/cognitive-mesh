"""The `workspace` commands (ADR-0036) through main() and in separate processes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from workspace_support import FIXTURE


def run(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def ws(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    path = tmp_path / "ws.sqlite3"
    for name in ("lib_core", "web_app", "shop_ui", "ui_kit"):
        code, out, err = run(capsys, "workspace", "add", FIXTURE / name, "--workspace", path)
        assert code == 0, err
        assert out.startswith("added ")
    return path


def test_graph_json_shows_both_sides_and_list_persists_in_a_new_process(ws: Path) -> None:
    env_cmd = [sys.executable, "-m", "cognitive_mesh"]
    graph = subprocess.run(  # noqa: S603
        [*env_cmd, "workspace", "graph", "--workspace", str(ws), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(graph.stdout)
    pairs = {(e["source"], e["target"]) for e in data["detected"]}
    assert pairs == {("web-app@web_app", "lib-core@lib_core"), ("shop-ui@shop_ui", "ui_kit")}
    edge = data["detected"][0]
    assert edge["declared_in"] and edge["published_in"]["line"] is not None
    listed = subprocess.run(  # noqa: S603
        [*env_cmd, "workspace", "list", "--workspace", str(ws)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert listed.stdout.count("[local]") == 4


def test_name_collision_remove_and_history(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "workspace", "add", FIXTURE / "lib_core", "--workspace", ws)
    assert code == 19 and "already in the workspace" in err
    code, out, _ = run(capsys, "workspace", "list", "--workspace", ws, "--json")
    names = [p["name"] for p in json.loads(out)]
    target = next(n for n in names if "lib" in n)
    assert run(capsys, "workspace", "remove", target, "--workspace", ws)[0] == 0
    code, out, _ = run(capsys, "workspace", "graph", "--workspace", ws, "--json")
    assert json.loads(out)["detected"][0]["source"] != target
    assert len(json.loads(out)["detected"]) == 1


def test_declared_link_is_labelled_and_mermaid_works(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "workspace", "list", "--workspace", ws, "--json")
    names = [p["name"] for p in json.loads(out)]
    a, b = names[1], names[2]
    code, _, err = run(
        capsys,
        "workspace",
        "link",
        a,
        b,
        "--kind",
        "queue",
        "--note",
        "hidden",
        "--by",
        "me",
        "--workspace",
        ws,
    )
    assert code == 0, err
    code, out, _ = run(capsys, "workspace", "graph", "--workspace", ws)
    assert "declared by a person (not detected): 1" in out
    code, out, _ = run(capsys, "workspace", "graph", "--workspace", ws, "--format", "mermaid")
    assert code == 0 and out.startswith("%%") and "-.->" in out


def test_errors_exit_19_and_create_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "none.sqlite3"
    for sub in ("list", "graph", "refresh"):
        code, _, err = run(capsys, "workspace", sub, "--workspace", missing)
        assert code == 19 and "does not exist" in err
    assert not missing.exists()
    code, _, err = run(capsys, "workspace", "add", tmp_path / "nope", "--workspace", missing)
    assert code == 19 and "does not exist" in err
    assert not missing.exists()
    code, _, err = run(
        capsys, "workspace", "add", "https://evil.example/o/r", "--workspace", missing
    )
    assert code == 19 and "only https://github.com" in err
    assert not missing.exists()
    foreign = tmp_path / "f.sqlite3"
    foreign.write_bytes(b"not sqlite at all, just text" * 10)
    code, _, err = run(capsys, "workspace", "list", "--workspace", foreign)
    assert code == 19
