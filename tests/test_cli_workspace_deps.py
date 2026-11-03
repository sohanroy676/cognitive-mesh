"""`workspace deps` and the new edge kinds in `workspace graph` (ADR-0037)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from contracts_support import FASTAPI, write_project

CLIENT = "import requests\nrequests.get('/users')\nrequests.get('/nowhere')\n"


def run(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def ws(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    api = write_project(tmp_path / "api", {"app.py": FASTAPI})
    web = write_project(tmp_path / "web", {"client.py": CLIENT})
    path = tmp_path / "ws.sqlite3"
    for name, folder in (("api", api), ("web", web)):
        code, _, err = run(capsys, "workspace", "add", folder, "--name", name, "--workspace", path)
        assert code == 0, err
    return path


def test_deps_text_reads_as_sentences_with_evidence(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "workspace", "deps", "web", "--workspace", ws)
    assert code == 0
    assert "web calls GET /users on api (exact)" in out
    assert "web:client.py:2" in out and "api:app.py:" in out
    assert "[api] no_provider" in out  # the call nothing serves is listed, not dropped
    code, out, _ = run(capsys, "workspace", "deps", "api", "--workspace", ws)
    assert "is called by (incoming):\n  web calls GET /users on api" in out


def test_deps_json_and_graph_json_agree(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, deps, _ = run(capsys, "workspace", "deps", "web", "--workspace", ws, "--json")
    _, graph, _ = run(capsys, "workspace", "graph", "--workspace", ws, "--json")
    assert json.loads(deps)["api"] == json.loads(graph)["api"]
    assert json.loads(graph)["api"][0]["origin"] == "detected"


def test_graph_text_and_mermaid_show_the_new_kinds(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, text, _ = run(capsys, "workspace", "graph", "--workspace", ws)
    assert "detected API contract edges: 1" in text
    assert "[api] no_provider: 1" in text
    _, mermaid, _ = run(capsys, "workspace", "graph", "--workspace", ws, "--format", "mermaid")
    assert "api GET /users #40;exact#41;" in mermaid  # parentheses are encoded


def test_unknown_project_and_missing_file(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "workspace", "deps", "nope", "--workspace", ws)
    assert code == 19 and "no project has that name" in err
    missing = tmp_path / "missing.sqlite3"
    code, _, _ = run(capsys, "workspace", "deps", "web", "--workspace", missing)
    assert code == 19 and not missing.exists()


def test_deps_in_a_new_process(ws: Path) -> None:
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "cognitive_mesh",
            "workspace",
            "deps",
            "web",
            "--workspace",
            str(ws),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "web calls GET /users on api (exact)" in done.stdout
