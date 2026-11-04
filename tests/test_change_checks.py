"""Running the declared check (ADR-0042): exit codes, timeout, environment, redaction."""

import sys
import time
from pathlib import Path

import pytest

from cognitive_mesh.change.checks import (
    ISOLATION,
    TAIL_CHARS,
    CheckStatus,
    check_env,
    run_check,
)

PY = sys.executable
FAKE_PARTS = ("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")


def py(code: str, tmp: Path, timeout: int = 30, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return run_check([PY, "-c", code], tmp, timeout, env=env)


def test_exit_codes_map_to_statuses(tmp_path: Path) -> None:
    ok = py("print('fine')", tmp_path)
    assert (ok.status, ok.exit_code, ok.isolation) == (CheckStatus.SUCCEEDED, 0, ISOLATION)
    assert "fine" in ok.output_tail
    bad = py("import sys; print('boom'); sys.exit(3)", tmp_path)
    assert (bad.status, bad.exit_code) == (CheckStatus.FAILED, 3)
    assert "boom" in bad.output_tail


def test_a_missing_command_does_not_raise(tmp_path: Path) -> None:
    gone = run_check(["definitely-not-a-command-xyz"], tmp_path, 5)
    assert gone.status is CheckStatus.NOT_STARTED and gone.exit_code is None


def test_a_hanging_test_is_killed_with_its_children(tmp_path: Path) -> None:
    flag = tmp_path / "child-alive"
    child = tmp_path / "child.py"
    child.write_text(
        "\n".join(
            [
                "import pathlib, sys, time",
                "time.sleep(1.5)",
                "pathlib.Path(sys.argv[1]).write_text('x')",
            ]
        ),
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "\n".join(
            [
                "import subprocess, sys, time",
                "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])",
                "time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    start = time.monotonic()
    result = run_check([PY, str(parent), str(child), str(flag)], tmp_path, 1)
    assert result.status is CheckStatus.TIMEOUT and result.exit_code is None
    assert time.monotonic() - start < 20
    time.sleep(2.5)
    assert not flag.exists()  # the grandchild died with the tree


def test_the_environment_is_an_allowlist_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_name = "COGMESH_TEST_" + "SECRET"  # built at run time
    monkeypatch.setenv(secret_name, "should-not-leak")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_not_leak")
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
    env = check_env()
    assert secret_name not in env and "GROQ_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9" and env["UV_OFFLINE"] == "1"
    assert env["NO_PROXY"] == "" and env["PIP_NO_INDEX"] == "1"
    seen = py(
        "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k "
        "or 'SECRET' in k))",
        tmp_path,
    )
    assert seen.output_tail.strip() == "[]"


def test_a_test_that_uses_the_network_through_proxy_settings_fails(tmp_path: Path) -> None:
    code = (
        "import urllib.request, sys\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=5)\n"
        "except Exception:\n"
        "    sys.exit(7)\n"
    )
    result = py(code, tmp_path)
    assert (result.status, result.exit_code) == (CheckStatus.FAILED, 7)
    assert result.isolation == "env_only"  # and the label says this is not real isolation


def test_output_is_redacted_and_cut_to_the_tail(tmp_path: Path) -> None:
    token = "".join(FAKE_PARTS)
    code = f"print('x' * 50000); [print('line', i) for i in range(5000)]; print('{token}')"
    start = time.monotonic()
    result = py(code, tmp_path)
    assert time.monotonic() - start < 30  # a long unbroken line must not make redaction crawl
    assert token not in result.output_tail
    assert len(result.output_tail) <= TAIL_CHARS
    assert "line 4999" in result.output_tail


def test_a_long_line_is_not_shown_at_all(tmp_path: Path) -> None:
    result = py("print('y' * 5000)", tmp_path)
    assert "yyyy" not in result.output_tail
    assert "5000 characters is not shown" in result.output_tail


def test_the_command_runs_in_the_given_folder_without_stdin(tmp_path: Path) -> None:
    result = py("import os,sys; print(os.getcwd()); print(repr(sys.stdin.read()))", tmp_path)
    assert str(tmp_path.resolve()).lower() in result.output_tail.lower()
    assert "''" in result.output_tail


def test_a_bare_command_name_is_never_taken_from_the_project_folder(tmp_path: Path) -> None:
    script = tmp_path / ("tool.cmd" if sys.platform == "win32" else "tool")
    script.write_text("echo ran" if sys.platform == "win32" else "#!/bin/sh\necho ran\n")
    script.chmod(0o755)
    result = run_check(["tool"], tmp_path, 5)
    assert result.status is CheckStatus.NOT_STARTED
    # a path the person wrote is theirs
    explicit = run_check([str(script)], tmp_path, 5)
    assert explicit.status is CheckStatus.SUCCEEDED
