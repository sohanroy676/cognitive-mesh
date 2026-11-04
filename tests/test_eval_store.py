"""The evaluation file is append-only and keeps unfinished items visible (ADR-0043)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.evaluate.errors import EvalError
from cognitive_mesh.evaluate.store import Arm, EvalStore, ItemState

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def begin(store: EvalStore) -> int:
    return store.begin_eval(
        taskset_name="demo",
        taskset_sha256="a" * 64,
        model_entry="m1",
        catalog_sha256=None,
        repeats=1,
        task_count=1,
        checks_allowed=False,
        now=NOW,
    )


def item(store: EvalStore, eval_id: int, arm: Arm = Arm.OFF) -> int:
    return store.begin_item(
        eval_id, 1, "t1", arm, claims_offered=0, past_changes_offered=0, prep_s=0.0, now=NOW
    )


def test_unfinished_item_reads_back_interrupted_in_a_new_connection(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite3"
    with EvalStore.create(path) as store:
        eid = begin(store)
        done = item(store, eid, Arm.OFF)
        item(store, eid, Arm.ON)
        store.finish_item(
            done,
            aborted=False,
            change_id="c1",
            run_ids=("r1", "r2"),
            reason="",
            refresh_s=0.0,
            now=NOW,
        )
    with EvalStore.open_existing(path) as again:
        states = {i.arm: i for i in again.items(eid)}
        assert states[Arm.OFF].state is ItemState.FINISHED
        assert states[Arm.OFF].run_ids == ("r1", "r2")
        assert states[Arm.ON].state is ItemState.INTERRUPTED
        assert again.evals()[0].finished_at is None


def test_aborted_item_keeps_its_reason(tmp_path: Path) -> None:
    with EvalStore.create(tmp_path / "e.sqlite3") as store:
        eid = begin(store)
        store.finish_item(
            item(store, eid),
            aborted=True,
            change_id=None,
            run_ids=(),
            reason="change_not_started",
            refresh_s=0.0,
            now=NOW,
        )
        (only,) = store.items(eid)
        assert only.state is ItemState.ABORTED and only.reason == "change_not_started"


def test_records_cannot_be_changed_or_deleted(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite3"
    with EvalStore.create(path) as store:
        begin(store)
    raw = sqlite3.connect(str(path), isolation_level=None)
    for sql in (
        "UPDATE evals SET model_entry = 'x'",
        "DELETE FROM evals",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute(sql)
    raw.close()


def test_duplicate_item_and_read_only_writes_refused(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite3"
    with EvalStore.create(path) as store:
        eid = begin(store)
        item(store, eid)
        with pytest.raises(EvalError):
            item(store, eid)
    with EvalStore.open_existing(path) as ro, pytest.raises(EvalError):
        begin(ro)


def test_foreign_and_missing_files_refused(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE x (a)")
    conn.commit()
    conn.close()
    with pytest.raises(EvalError):
        EvalStore.create(other)
    with pytest.raises(EvalError):
        EvalStore.open_existing(other)
    with pytest.raises(EvalError):
        EvalStore.open_existing(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()
