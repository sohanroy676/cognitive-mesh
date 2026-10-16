import json
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_INGESTION_ERROR,
    EXIT_OK,
    _printable,
    main,
)

SECRET = "gsk_test_secret_value_123"  # noqa: S105 - fake value for tests
FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"


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
