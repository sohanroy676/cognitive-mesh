"""The declared check command (ADR-0042): person-declared, version 1 files migrate."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from cognitive_mesh.workspace import ProjectKind, WorkspaceError, WorkspaceStore
from cognitive_mesh.workspace.fetch import run_git_with


def make(path: Path) -> None:
    with WorkspaceStore.create(path) as store:
        store.add_project("one", ProjectKind.LOCAL, "/x/one")


def test_check_persists_and_newest_wins(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store:
        assert store.check_command("one") is None
        store.set_check("one", ["pytest", "-q"], by="me")
        store.set_check("one", ["pytest", "-x"], timeout_s=60, by="me")
    with WorkspaceStore.open_existing(path) as store:
        got = store.check_command("one")
    assert got is not None
    assert got.argv == ("pytest", "-x")
    assert got.timeout_s == 60


@pytest.mark.parametrize(
    ("argv", "timeout"),
    [
        ([], 10),
        ([""], 10),
        (["a\nb"], 10),
        (["x"] * 51, 10),
        (["x" * 501], 10),
        (["x"], 0),
        (["x"], 3601),
    ],
)
def test_bad_check_commands_are_refused(tmp_path: Path, argv: list[str], timeout: int) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store, pytest.raises(WorkspaceError):
        store.set_check("one", argv, timeout_s=timeout, by="me")


def test_unknown_project_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store, pytest.raises(WorkspaceError, match="no project"):
        store.set_check("nope", ["x"], by="me")


def test_version_one_file_reads_and_migrates_on_write(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE checks")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    with WorkspaceStore.open_existing(path) as store:
        assert store.check_command("one") is None
        with pytest.raises(WorkspaceError):
            store.set_check("one", ["x"], by="me")
    with WorkspaceStore.create(path) as store:
        store.set_check("one", ["x"], by="me")
    with WorkspaceStore.open_existing(path) as store:
        assert store.check_command("one") is not None


def test_malformed_check_row_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store:
        store.set_check("one", ["x"], by="me")
    conn = sqlite3.connect(path)
    conn.execute("UPDATE checks SET argv_json = '{\"a\": 1}'")
    conn.commit()
    conn.close()
    with (
        WorkspaceStore.open_existing(path) as store,
        pytest.raises(WorkspaceError, match="malformed"),
    ):
        store.check_command("one")


def test_cli_set_and_show_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    ws = str(path)
    assert (
        main(
            ["workspace", "set-check", "--workspace", ws, "--by", "me", "one", "--", "pytest", "-q"]
        )
        == 0
    )
    assert main(["workspace", "show-check", "one", "--workspace", ws]) == 0
    out = capsys.readouterr().out
    assert "pytest -q" in out
    assert main(["workspace", "set-check", "--workspace", ws, "--by", "me", "one"]) == 19


def test_run_git_with_passes_extra_env_and_timeout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S607
    result = run_git_with(
        ["config", "--get", "cogtest.value"],
        tmp_path,
        env_extra={
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "cogtest.value",
            "GIT_CONFIG_VALUE_0": "yes",
        },
        timeout_s=30,
    )
    assert result.stdout.strip() == "yes"
