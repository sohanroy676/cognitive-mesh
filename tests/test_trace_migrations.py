"""Schema migrations of the trace store (ADR-0011 amendment)."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.cli import EXIT_OK, EXIT_TRACE_STORE_ERROR, main
from cognitive_mesh.traces import SCHEMA_VERSION, TraceStore, TraceStoreError
from cognitive_mesh.traces import store as store_module

FIXTURES = Path(__file__).parent / "fixtures"
ADD_NOTE = "ALTER TABLE runs ADD COLUMN note TEXT;"


def sql(db: Path, statement: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(statement).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def version(db: Path) -> int:
    return int(str(sql(db, "PRAGMA user_version")[0][0]))


def columns(db: Path) -> list[str]:
    return [str(r[1]) for r in sql(db, "PRAGMA table_info(runs)")]


def _record_ok_run(db: Path) -> None:
    args = ["run", str(FIXTURES / "plans" / "ok.json"), "--root", str(FIXTURES / "dep_app")]
    assert main([*args, "--trace-db", str(db)]) == EXIT_OK


@pytest.fixture
def v1_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database written by the baseline schema alone (as before ADR-0017), with one run."""
    db = tmp_path / "t.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "MIGRATIONS", {})
        _record_ok_run(db)
    assert version(db) == 1
    return db


@pytest.fixture
def current_db(tmp_path: Path) -> Path:
    db = tmp_path / "current.sqlite3"
    _record_ok_run(db)
    assert version(db) == SCHEMA_VERSION
    return db


def test_the_declared_version_matches_the_migrations() -> None:
    assert len(store_module.MIGRATIONS) + 1 == SCHEMA_VERSION
    assert sorted(store_module.MIGRATIONS) == list(range(1, SCHEMA_VERSION))


def test_a_writer_upgrades_an_older_database_and_keeps_its_runs(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = sql(v1_db, "SELECT run_id, status, state FROM runs")
    # a later migration stacked on the real one
    monkeypatch.setattr(store_module, "MIGRATIONS", {**store_module.MIGRATIONS, 2: ADD_NOTE})
    with TraceStore.create(v1_db) as store:
        assert [r.run_id for r in store.list().runs] == [str(before[0][0])]
    assert version(v1_db) == 3
    assert "note" in columns(v1_db)
    assert sql(v1_db, "SELECT run_id, status, state FROM runs") == before


def test_several_steps_apply_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "t.sqlite3"
    monkeypatch.setattr(
        store_module,
        "MIGRATIONS",
        {1: ADD_NOTE, 2: "ALTER TABLE runs ADD COLUMN extra TEXT;"},
    )
    TraceStore.create(db).close()  # a new database ends at the target version
    assert version(db) == 3
    assert {"note", "extra"} <= set(columns(db))


def test_a_current_database_is_left_alone(current_db: Path) -> None:
    before = current_db.read_bytes()
    TraceStore.create(current_db).close()
    assert current_db.read_bytes() == before


def test_the_real_migration_adds_run_context_and_keeps_old_runs(v1_db: Path) -> None:
    before = sql(v1_db, "SELECT run_id, status, state FROM runs")
    assert "run_context" not in str(sql(v1_db, "SELECT name FROM sqlite_master"))
    with TraceStore.create(v1_db) as store:
        (summary,) = store.list().runs
        old = store.get(summary.run_id)
    assert version(v1_db) == 2 == SCHEMA_VERSION
    assert sql(v1_db, "SELECT run_id, status, state FROM runs") == before
    assert sql(v1_db, "SELECT count(*) FROM run_context") == [(0,)]
    assert old.recalled is None and old.request_length is None  # an older run was offered nothing


def test_a_reader_refuses_the_real_v1_database_without_changing_it(v1_db: Path) -> None:
    before = v1_db.read_bytes()
    with pytest.raises(TraceStoreError, match="older version"):
        TraceStore.open_existing(v1_db)
    assert v1_db.read_bytes() == before
    assert version(v1_db) == 1


def test_readers_never_upgrade_and_say_what_to_do(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MIGRATIONS", {1: ADD_NOTE})
    before = v1_db.read_bytes()
    with pytest.raises(TraceStoreError, match="older version"):
        TraceStore.open_existing(v1_db)
    assert v1_db.read_bytes() == before
    assert main(["traces", "list", "--db", str(v1_db)]) == EXIT_TRACE_STORE_ERROR
    assert version(v1_db) == 1


def test_a_failing_migration_rolls_back_completely(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = f"{ADD_NOTE}\nTHIS IS NOT SQL;"
    monkeypatch.setattr(store_module, "MIGRATIONS", {1: broken})
    with pytest.raises(TraceStoreError) as err:
        TraceStore.create(v1_db)
    assert "THIS IS NOT SQL" not in str(err.value)
    assert "ALTER" not in str(err.value)
    assert version(v1_db) == 1
    assert "note" not in columns(v1_db)


def test_a_later_step_failing_keeps_the_earlier_completed_steps(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MIGRATIONS", {1: ADD_NOTE, 2: "NOT SQL;"})
    with pytest.raises(TraceStoreError):
        TraceStore.create(v1_db)
    assert version(v1_db) == 2
    assert "note" in columns(v1_db)


def test_a_gap_in_the_migrations_is_an_error_not_a_guess(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MIGRATIONS", {5: ADD_NOTE})
    with pytest.raises(TraceStoreError, match="no migration"):
        TraceStore.create(v1_db)
    assert version(v1_db) == 1


def test_a_newer_database_is_refused_and_untouched(v1_db: Path) -> None:
    sql(v1_db, f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    before = v1_db.read_bytes()
    with pytest.raises(TraceStoreError, match="newer"):
        TraceStore.create(v1_db)
    assert v1_db.read_bytes() == before


def test_an_upgraded_database_reads_in_a_new_connection(
    v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MIGRATIONS", {**store_module.MIGRATIONS, 2: ADD_NOTE})
    TraceStore.create(v1_db).close()
    assert main(["traces", "list", "--db", str(v1_db)]) == EXIT_OK
