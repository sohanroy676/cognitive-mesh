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
