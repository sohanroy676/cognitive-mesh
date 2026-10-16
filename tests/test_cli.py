import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import EXIT_CONFIG_ERROR, EXIT_OK, main

SECRET = "gsk_test_secret_value_123"  # noqa: S105 - fake value for tests


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
