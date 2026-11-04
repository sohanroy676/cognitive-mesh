"""Changes in the trace database (ADR-0042): append-only, migrated, status derived from events."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.change.models import (
    ChangeEvent,
    ChangeRecord,
    ChangeStatus,
    EventKind,
    approval_for,
    derive_status,
)
from cognitive_mesh.traces import TraceStore, TraceStoreError
from cognitive_mesh.traces import store as store_module

NOW = datetime(2026, 11, 4, tzinfo=UTC)
C1, C2 = "a" * 40, "b" * 40
D1, D2 = "1" * 64, "2" * 64


def record(change_id: str = "ab12cd34ef56") -> ChangeRecord:
    return ChangeRecord(
        change_id=change_id,
        project="api",
        app_id="api",
        base_branch="main",
        base_commit="c" * 40,
        branch=f"cogmesh/change-{change_id}",
        worktree="/w",
        max_files=3,
        max_lines=100,
        request_len=12,
        created_at=NOW,
    )


def ev(
    kind: EventKind, commit: str | None = None, digest: str | None = None, detail: str = ""
) -> ChangeEvent:
    return ChangeEvent(
        change_id="ab12cd34ef56", kind=kind, commit=commit, diff_sha256=digest, detail=detail,
        created_at=NOW,
    )  # fmt: skip


def test_changes_and_events_persist_in_order(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        store.add_change(record())
        store.add_change_event(ev(EventKind.APPLIED, C1, D1))
        store.add_change_event(ev(EventKind.CHECK, C1, D1, "succeeded; isolation env_only"))
    with TraceStore.open_existing(db) as store:
        ((head, events),) = store.read_changes()
    assert head == record()
    assert [e.kind for e in events] == [EventKind.APPLIED, EventKind.CHECK]
    assert derive_status(events) is ChangeStatus.CHECK_PASSED


def test_rows_cannot_be_changed_or_deleted_and_events_need_a_change(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        store.add_change(record())
        store.add_change_event(ev(EventKind.APPLIED, C1, D1))
        with pytest.raises(TraceStoreError, match="already recorded"):
            store.add_change(record())
        orphan = ev(EventKind.APPLIED, C1, D1).model_copy(update={"change_id": "f" * 12})
        with pytest.raises(TraceStoreError, match="not recorded"):
            store.add_change_event(orphan)
    conn = sqlite3.connect(db)
    for sql in (
        "UPDATE change_events SET detail = 'x'",
        "DELETE FROM change_events",
        "UPDATE changes SET project = 'x'",
        "DELETE FROM changes",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql)
    conn.close()


def test_a_version_seven_database_is_upgraded_and_keeps_its_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(
            store_module, "MIGRATIONS", {k: v for k, v in store_module.MIGRATIONS.items() if k < 7}
        )
        TraceStore.create(db).close()
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone() == (7,)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'changes'").fetchone() == (
        0,
    )
    conn.close()
    with pytest.raises(TraceStoreError, match="older version"):
        TraceStore.open_existing(db)
    with TraceStore.create(db) as store:
        store.add_change(record())
    with TraceStore.open_existing(db) as store:
        assert len(store.read_changes()) == 1


def test_status_follows_the_events_and_a_stale_approval_does_not_count() -> None:
    applied1 = ev(EventKind.APPLIED, C1, D1)
    assert derive_status([]) is ChangeStatus.NO_DRAFT
    assert (
        derive_status([ev(EventKind.REFUSED, detail="refused:bad_path")]) is ChangeStatus.NO_DRAFT
    )
    assert derive_status([applied1]) is ChangeStatus.UNCHECKED
    failed = ev(EventKind.CHECK, C1, D1, "failed; isolation env_only")
    assert derive_status([applied1, failed]) is ChangeStatus.CHECK_FAILED
    approved = ev(EventKind.APPROVED, C1, D1)
    assert derive_status([applied1, failed, approved]) is ChangeStatus.APPROVED
    # a newer commit: the approval and the check belonged to the old one
    applied2 = ev(EventKind.APPLIED, C2, D2)
    events = [applied1, failed, approved, applied2]
    assert approval_for(events, C2, D2) is None
    assert derive_status(events) is ChangeStatus.UNCHECKED
    passed = ev(EventKind.CHECK, C2, D2, "succeeded; isolation env_only")
    assert derive_status([*events, passed]) is ChangeStatus.CHECK_PASSED
    pushed = ev(EventKind.PUSHED, C2, D2)
    assert derive_status([*events, passed, pushed]) is ChangeStatus.PUSHED
    # the approval names the commit but not this diff: it does not count
    assert approval_for([ev(EventKind.APPROVED, C2, D1)], C2, D2) is None
    assert derive_status([*events, ev(EventKind.DISCARDED)]) is ChangeStatus.DISCARDED


def test_a_check_that_did_not_finish_is_not_a_pass() -> None:
    events = [ev(EventKind.APPLIED, C1, D1), ev(EventKind.CHECK, C1, D1, "timeout; isolation x")]
    assert derive_status(events) is ChangeStatus.CHECK_FAILED


def test_a_stale_migration_run_never_sets_the_version_back(tmp_path: Path) -> None:
    """Process A finished (version 8); process B still believes 6. Script 6 succeeds twice on its
    own, so without the re-read under the lock B would set the version back to 7."""
    db = tmp_path / "t.sqlite3"
    TraceStore.create(db).close()
    conn = sqlite3.connect(db, isolation_level=None)
    store_module._migrate(conn, 6)
    assert conn.execute("PRAGMA user_version").fetchone() == (store_module.SCHEMA_VERSION,)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'changes'").fetchone() == (
        1,
    )
    conn.close()
