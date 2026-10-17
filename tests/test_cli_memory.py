"""`memory index` / `memory search` commands (ADR-0014), with a fake embedder: no network."""

import json
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import EXIT_MEMORY_ERROR, EXIT_OK, EXIT_TRACE_STORE_ERROR, main
from memory_support import FakeEmbedder, build_fixture_db


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.sqlite3"
    build_fixture_db(path)
    return path


def run(*args: str | Path) -> int:
    return main([str(a) for a in args])


def test_index_then_search_finds_the_relevant_run(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = tmp_path / "idx"
    assert run("memory", "index", "--db", db, "--index", idx) == EXIT_OK
    assert "3 added" in capsys.readouterr().out
    assert run("memory", "search", "invoice sales tax", "--db", db, "--index", idx) == EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[0].split()[1] == "run-invoice"
    assert "not evidence that a past result was correct" in out


def test_search_json_has_hits_note_and_no_hit_message(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = tmp_path / "idx"
    run("memory", "index", "--db", db, "--index", idx)
    capsys.readouterr()
    args = ("memory", "search", "invoice", "--db", db, "--index", idx, "--json")
    assert run(*args) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["hits"][0]["run_id"] == "run-invoice"
    assert data["note"]
    assert run(*args[:2], "zebra quantum", *args[3:], "--min-similarity", "0.9") == EXIT_OK
    empty = json.loads(capsys.readouterr().out)
    assert empty["hits"] == []
    assert empty["message"] == "no sufficiently similar experience"


def test_index_twice_reports_unchanged(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = tmp_path / "idx"
    run("memory", "index", "--db", db, "--index", idx)
    capsys.readouterr()
    assert run("memory", "index", "--db", db, "--index", idx, "--json") == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert (report["added"], report["updated"], report["unchanged"]) == (0, 0, 3)


def test_search_without_an_index_exits_11_and_creates_nothing(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = tmp_path / "missing"
    assert run("memory", "search", "x", "--db", db, "--index", idx) == EXIT_MEMORY_ERROR
    assert "does not exist" in capsys.readouterr().err
    assert not idx.exists()


def test_missing_trace_database_exits_8_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.sqlite3"
    assert run("memory", "index", "--db", missing, "--index", tmp_path / "i") == (
        EXIT_TRACE_STORE_ERROR
    )
    assert "does not exist" in capsys.readouterr().err
    assert not missing.exists()


def test_incompatible_model_exits_11(
    db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = tmp_path / "idx"
    run("memory", "index", "--db", db, "--index", idx)
    capsys.readouterr()
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder(name="other"))
    assert run("memory", "search", "x", "--db", db, "--index", idx) == EXIT_MEMORY_ERROR
    assert "different embedding model" in capsys.readouterr().err


def test_bad_arguments_are_a_usage_style_error_not_a_crash(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = tmp_path / "idx"
    run("memory", "index", "--db", db, "--index", idx)
    capsys.readouterr()
    code = run("memory", "search", "  ", "--db", db, "--index", idx)
    assert code == cli.EXIT_CONFIG_ERROR
    assert "query is empty" in capsys.readouterr().err


def test_malformed_run_is_reported_with_exit_8(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = 'run-deps'")
    conn.commit()
    conn.close()
    assert run("memory", "index", "--db", db, "--index", tmp_path / "i") == EXIT_TRACE_STORE_ERROR
    assert "run-deps" in capsys.readouterr().err
