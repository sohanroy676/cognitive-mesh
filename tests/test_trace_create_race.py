"""Creating or upgrading the trace database while another process does the same.

Found by running several `task` processes at once against a database that did not exist yet: the
losers failed with "cannot upgrade the trace database". The version is read, then acted on; another
process can create or migrate the database in between. The first two tests model that interleaving
by making the first version read stale; the last one starts real processes.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.traces import SCHEMA_VERSION, TraceStore, TraceStoreError
from cognitive_mesh.traces import store as store_module

ONE_PROCESS = (
    "import sys; from pathlib import Path; "
    "from cognitive_mesh.traces import TraceStore; "
    "TraceStore.create(Path(sys.argv[1])).close()"
)


def version(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def stale_first_read(monkeypatch: pytest.MonkeyPatch, stale: int) -> None:
    real = store_module._user_version
    calls = {"n": 0}

    def read(conn: sqlite3.Connection) -> int:
        calls["n"] += 1
        return stale if calls["n"] == 1 else real(conn)

    monkeypatch.setattr(store_module, "_user_version", read)


def test_a_migration_another_process_already_applied_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    TraceStore.create(db).close()  # the "other process" finished: current schema
    stale_first_read(monkeypatch, 1)  # ... but this process read the version before that
    TraceStore.create(db).close()
    assert version(db) == SCHEMA_VERSION


def test_a_database_another_process_just_created_is_not_foreign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    TraceStore.create(db).close()
    stale_first_read(monkeypatch, 0)  # this process saw an empty file, then the tables appeared
    TraceStore.create(db).close()
    assert version(db) == SCHEMA_VERSION


def test_a_foreign_database_is_still_refused(tmp_path: Path) -> None:
    db = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notes (x TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(TraceStoreError, match="not a trace database"):
        TraceStore.create(db)
    assert version(db) == 0  # nothing was changed in it


def test_a_failing_migration_that_nobody_else_applied_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "MIGRATIONS", {})
        TraceStore.create(db).close()  # a version-1 database
    monkeypatch.setattr(store_module, "MIGRATIONS", {1: "CREATE TABLE runs (x TEXT);"})
    with pytest.raises(TraceStoreError, match="cannot upgrade"):
        TraceStore.create(db)
    assert version(db) == 1  # rolled back, not half-applied


@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_many_processes_creating_one_new_database_all_succeed(
    tmp_path: Path, round_no: int
) -> None:
    db = tmp_path / f"t{round_no}.sqlite3"
    procs = [
        subprocess.Popen(  # noqa: S603  (our own interpreter)
            [sys.executable, "-c", ONE_PROCESS, str(db)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(8)
    ]
    outputs = [(p.wait(timeout=120), p.stdout.read() if p.stdout else "") for p in procs]
    assert [code for code, _ in outputs] == [0] * 8, outputs
    assert version(db) == SCHEMA_VERSION
