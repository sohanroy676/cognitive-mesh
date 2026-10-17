"""What a run was offered is recorded with it and read back in a later session (ADR-0017)."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognitive_mesh.agents.context import ClaimItem, RecalledContext
from cognitive_mesh.cli import EXIT_OK, main
from cognitive_mesh.traces import (
    TraceRecorder,
    TraceRecordError,
    TraceStore,
    TraceStoreError,
)
from cognitive_mesh.traces.models import RunTrace, TraceEnvironment, TraceStatus
from test_traces import execute, fake_secret, good, step, ticking

PACK_CLAIM = ClaimItem(
    claim_id="claim:" + "ab" * 16,
    name="a imports b",
    predicate="depends_on",
    subject="module:a.py",
    object="module:b.py",
    detail="imports",
    status="stale",
    meaning="observed only on other code versions",
)


def pack(name: str = "a imports b") -> RecalledContext:
    return RecalledContext(claims=(PACK_CLAIM.model_copy(update={"name": name}),), empty=False)


def record_with(db: Path, recalled: RecalledContext | None, run_id: str = "run-1") -> None:
    plan, run = execute(lambda t, n: good(t), step("only"))
    with TraceStore.create(db) as store:
        recorder = TraceRecorder(store, now=ticking(), new_run_id=lambda: run_id)
        rid = recorder.begin(plan, recalled=recalled, request_length=42 if recalled else None)
        recorder.finish(rid, run)


def read(db: Path, run_id: str = "run-1") -> RunTrace:
    with TraceStore.open_existing(db) as store:
        return store.get(run_id)


def sql(db: Path, statement: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def test_the_offered_context_is_stored_and_read_back_in_a_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, pack())
    trace = read(db)
    assert trace.recalled == pack() and trace.request_length == 42
    assert trace.recalled.claims[0].status == "stale"  # the status it had when it was offered
    assert trace.status is TraceStatus.SUCCEEDED  # the outcome is the run's own, derived status


def test_a_run_without_context_reads_back_with_none(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, None)
    trace = read(db)
    assert trace.recalled is None and trace.request_length is None


def test_the_empty_marker_is_recorded_too(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, RecalledContext.none())
    trace = read(db)
    assert trace.recalled is not None and trace.recalled.empty and trace.request_length == 42


def test_a_run_that_never_finished_still_shows_what_it_was_offered(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    plan, _ = execute(lambda t, n: good(t), step("only"))
    with TraceStore.create(db) as store:
        TraceRecorder(store, now=ticking(), new_run_id=lambda: "run-1").begin(
            plan, recalled=pack(), request_length=7
        )
    trace = read(db)
    assert trace.status is TraceStatus.INTERRUPTED and trace.recalled == pack()


def test_secrets_in_the_pack_never_reach_the_database(tmp_path: Path) -> None:
    secret = fake_secret()
    db = tmp_path / "t.sqlite3"
    record_with(db, pack(name=f"a imports b key={secret}"))
    assert secret.encode() not in db.read_bytes()
    trace = read(db)
    assert trace.recalled is not None and "[REDACTED:" in trace.recalled.claims[0].name
    assert trace.redactions and all(secret not in r for r in trace.redactions)


def test_the_header_and_its_context_are_written_together_or_not_at_all(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    TraceStore.create(db).close()
    sql(db, "DROP TABLE run_context")  # make the second insert fail
    header = RunTrace(
        run_id="run-1",
        task_id="t",
        status=TraceStatus.INTERRUPTED,
        started_at=datetime(2026, 10, 17, tzinfo=UTC),
        environment=TraceEnvironment(cognitive_mesh_version="t", python_version="3", platform="t"),
        plan={},
        recalled=pack(),
        request_length=5,
    )
    with TraceStore.create(db) as store, pytest.raises(TraceStoreError) as err:
        store.begin_run(header)
    assert "sqlite error" in str(err.value) and "claim:" not in str(err.value)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0  # no half-written run
    finally:
        conn.close()


CORRUPTIONS = {
    "garbage": "UPDATE run_context SET context_json = 'SECRETMARK'",
    "wrong_shape": "UPDATE run_context SET context_json = '{\"claims\": 5}'",
    "empty_lies": "UPDATE run_context SET context_json = "
    "json_set(context_json, '$.empty', json('true'))",
    "unknown_field": "UPDATE run_context SET context_json = json_set(context_json, '$.extra', 'x')",
}


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_a_corrupted_context_is_reported_not_trusted(tmp_path: Path, name: str) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, pack(), run_id="bad")
    record_with(db, pack(), run_id="good")
    sql(db, CORRUPTIONS[name] + " WHERE run_id = 'bad'")
    with TraceStore.open_existing(db) as store:
        with pytest.raises(TraceRecordError) as err:
            store.get("bad")
        assert "bad" in str(err.value) and "SECRETMARK" not in str(err.value)
        listing = store.list()
        assert store.get("good").recalled == pack()  # one bad row does not hide the others
    assert listing.malformed == ("bad",)


def test_context_and_request_length_are_recorded_together() -> None:
    kwargs = {
        "run_id": "r",
        "task_id": "t",
        "status": TraceStatus.INTERRUPTED,
        "started_at": datetime(2026, 10, 17, tzinfo=UTC),
        "environment": TraceEnvironment(
            cognitive_mesh_version="t", python_version="3", platform="t"
        ),
        "plan": {},
    }
    with pytest.raises(ValidationError):
        RunTrace(**kwargs, recalled=pack())
    with pytest.raises(ValidationError):
        RunTrace(**kwargs, request_length=3)


def test_traces_show_prints_what_the_run_was_offered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, pack())
    assert main(["traces", "show", "run-1", "--db", str(db)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "offered context: 1 claim(s), 0 raw trace(s)" in out
    assert "[stale] a imports b" in out and "none of this is verified truth" in out


def test_traces_show_json_includes_the_context_and_plain_runs_say_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, pack(), run_id="with")
    record_with(db, None, run_id="plain")
    assert main(["traces", "show", "with", "--db", str(db), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["recalled"]["claims"][0]["status"] == "stale"
    assert main(["traces", "show", "plain", "--db", str(db)]) == EXIT_OK
    assert "offered context" not in capsys.readouterr().out
