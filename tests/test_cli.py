import json
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_GRAPH_ERROR,
    EXIT_INGESTION_ERROR,
    EXIT_OK,
    EXIT_OUTPUT_ERROR,
    _printable,
    main,
)

SECRET = "gsk_test_secret_value_123"  # noqa: S105 - fake value for tests
FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"
DEP_FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
JS_FIXTURE = Path(__file__).parent / "fixtures" / "js_app"


def test_main_succeeds_with_valid_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    assert main([]) == EXIT_OK
    out = capsys.readouterr()
    assert "config OK (provider=groq)" in out.out
    assert SECRET not in out.out + out.err


def test_main_reports_missing_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_CONFIG_ERROR
    out = capsys.readouterr()
    assert out.out == ""
    assert "COGMESH_PROVIDER" in out.err


def test_module_runs_from_unrelated_cwd(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cognitive_mesh"],
        cwd=isolated_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert "config OK (provider=ollama)" in result.stdout


def test_ingest_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", str(FIXTURE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "9 files" in out
    assert "src/app/main.py" in out
    assert "manifest" in out


def test_ingest_json_omits_content_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", str(FIXTURE), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total_files"] == 9
    assert all("content" not in f for f in payload["files"])
    assert all(len(f["ref"]["sha256"]) == 64 for f in payload["files"])


def test_ingest_json_include_content(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", str(FIXTURE), "--json", "--include-content"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert any("Sample App" in f["content"] for f in payload["files"])


def test_ingest_missing_path_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", "definitely-not-here"]) == EXIT_INGESTION_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not exist" in captured.err


def test_ingest_needs_no_provider_config(capsys: pytest.CaptureFixture[str]) -> None:
    # isolated_env clears COGMESH_PROVIDER; ingest must still work
    assert main(["ingest", str(FIXTURE)]) == EXIT_OK
    assert "COGMESH_PROVIDER" not in capsys.readouterr().err


def test_ingest_subcommand_via_module(isolated_env: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cognitive_mesh", "ingest", str(FIXTURE)],
        cwd=isolated_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert "9 files" in result.stdout


def test_ingest_text_escapes_control_characters_in_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "bad\x1b[31mname.png"
    try:
        (tmp_path / name).write_bytes(b"x")
    except OSError:
        pytest.skip("filesystem rejects control characters in file names")
    assert main(["ingest", str(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert r"bad\x1b[31mname.png" in out


def test_printable_escapes_control_characters() -> None:
    assert _printable("a\x1b[31mb\nc") == r"a\x1b[31mb\nc"
    assert _printable("plain/path.py") == "plain/path.py"


def test_graph_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(DEP_FIXTURE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "unresolved: unresolved:import:yaml" in out
    assert (
        "cycle: module:src/dep_app/a.py -> module:src/dep_app/b.py -> module:src/dep_app/a.py"
        in out
    )
    assert "transitive_unknown" in out


def test_graph_json_has_evidence_on_every_edge(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(DEP_FIXTURE), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["edges"] and all(e["evidence"] for e in payload["edges"])
    assert payload["summary"]["cycles"] == 1


def test_graph_deps_of_direct_and_transitive(capsys: pytest.CaptureFixture[str]) -> None:
    node = "module:src/dep_app/a.py"
    assert main(["graph", str(DEP_FIXTURE), "--deps-of", node]) == EXIT_OK
    direct = capsys.readouterr().out
    assert "stdlib:os" in direct and "stdlib:json" not in direct
    assert main(["graph", str(DEP_FIXTURE), "--deps-of", node, "--transitive"]) == EXIT_OK
    assert "stdlib:json" in capsys.readouterr().out


def test_graph_unknown_node_exits_4(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(DEP_FIXTURE), "--deps-of", "nope"]) == EXIT_GRAPH_ERROR
    out = capsys.readouterr()
    assert out.out == "" and "unknown node" in out.err


def test_graph_missing_root_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(tmp_path / "missing")]) == EXIT_INGESTION_ERROR
    assert "does not exist" in capsys.readouterr().err


def test_graph_needs_no_provider_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(FIXTURE)]) == EXIT_OK
    assert "no_manifest" not in capsys.readouterr().out  # sample_app has manifests


def test_graph_js_summary_shows_unresolved_and_limits(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(JS_FIXTURE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "unresolved: unresolved:dynamic:src/index.ts:9" in out
    assert "computed_import" in out and "js_resolution_limited" in out
    assert "nope" not in out


def test_graph_js_json_and_transitive_deps(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(JS_FIXTURE), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["edges"] and all(e["evidence"] for e in payload["edges"])
    node = "module:src/index.ts"
    assert main(["graph", str(JS_FIXTURE), "--deps-of", node, "--transitive"]) == EXIT_OK
    assert "stdlib:node:fs" in capsys.readouterr().out  # index -> util -> node:fs


# --- graph --format (v0.3.2) ------------------------------------------------------------------


def test_graph_text_output_is_unchanged_by_visualization(
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = (Path(__file__).parent / "fixtures" / "expected" / "dep_app.txt").read_text(
        encoding="utf-8"
    )
    assert main(["graph", str(DEP_FIXTURE)]) == EXIT_OK
    assert capsys.readouterr().out.replace("\r\n", "\n") == expected


def test_graph_format_mermaid_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(DEP_FIXTURE), "--format", "mermaid"]) == EXIT_OK
    out = capsys.readouterr()
    assert out.out.startswith("%% cognitive-mesh") and "flowchart TD" in out.out
    assert "==>" in out.out and "-.->" in out.out
    assert out.err == ""


def test_graph_format_html_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(JS_FIXTURE), "--format", "html"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("<!doctype html>") and "<svg" in out and "<script" not in out


def test_graph_format_output_writes_ascii_lf_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "g.html"
    assert main(["graph", str(DEP_FIXTURE), "--format", "html", "--output", str(target)]) == 0
    data = target.read_bytes()
    assert data.isascii() and b"\r" not in data and data.startswith(b"<!doctype html>")
    out = capsys.readouterr().out
    assert "wrote html graph" in out and "<svg" not in out


def test_graph_output_is_identical_across_runs(tmp_path: Path) -> None:
    first, second = tmp_path / "a.html", tmp_path / "b.html"
    for target in (first, second):
        assert main(["graph", str(JS_FIXTURE), "--format", "html", "--output", str(target)]) == 0
    assert first.read_bytes() == second.read_bytes()


def test_graph_output_to_missing_directory_exits_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "nope" / "g.html"
    args = ["graph", str(DEP_FIXTURE), "--format", "html", "--output", str(target)]
    assert main(args) == EXIT_OUTPUT_ERROR
    assert "cannot write output" in capsys.readouterr().err
    assert not target.parent.exists()


def test_graph_output_to_a_directory_exits_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["graph", str(DEP_FIXTURE), "--format", "mermaid", "--output", str(tmp_path)]
    assert main(args) == EXIT_OUTPUT_ERROR
    assert "cannot write output" in capsys.readouterr().err


def test_graph_format_truncation_notice_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["graph", str(DEP_FIXTURE), "--format", "html", "--max-nodes", "3"]) == EXIT_OK
    out = capsys.readouterr()
    assert "TRUNCATED: showing 3 of 19 nodes" in out.err
    assert "showing 3 of 19 nodes" in out.out  # banner in the page itself


def test_graph_format_missing_root_exits_3(tmp_path: Path) -> None:
    assert main(["graph", str(tmp_path / "missing"), "--format", "html"]) == EXIT_INGESTION_ERROR


@pytest.mark.parametrize(
    "extra",
    [
        ["--format", "html", "--json"],
        ["--format", "html", "--deps-of", "x"],
        ["--format", "html", "--transitive"],
        ["--output", "x.html"],
        ["--max-nodes", "5"],
        ["--max-edges", "5"],
        ["--format", "svg"],
        ["--format", "html", "--max-nodes", "0"],
        ["--format", "html", "--max-edges", "-1"],
        ["--format", "html", "--max-nodes", "many"],
    ],
)
def test_graph_format_usage_errors_exit_2(
    extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["graph", str(DEP_FIXTURE), *extra])
    assert exc.value.code == EXIT_CONFIG_ERROR
    out = capsys.readouterr()
    assert out.out == "" and out.err


def test_agents_lists_roles_without_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents"]) == EXIT_OK  # no COGMESH_PROVIDER set
    out = capsys.readouterr().out
    assert "dependency_analyst" in out and "code_explainer" in out
    assert "dependencies_of [deterministic]" in out
    assert "explain_file [uses model]" in out


def test_agents_json_has_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agents", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert [a["id"] for a in payload["agents"]] == ["code_explainer", "dependency_analyst"]
    for agent in payload["agents"]:
        for cap in agent["capabilities"]:
            assert cap["input_schema"]["type"] == "object"
            assert cap["output_schema"]["type"] == "object"
