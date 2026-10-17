"""Source checks: what happens to the graph when the trace database changes (ADR-0015)."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    DiagnosticCode,
    Snapshot,
    SourceStatus,
    build_from_traces,
)
from cognitive_mesh.experience.verify import SourceReport, check_sources
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db
from memory_support import add_run

A, B, C = "module:a.py", "module:b.py", "module:c.py"


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Snapshot]:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B, C])], "fp1"),
            "r2": ([deps_step("r2", "s1", A, [B])], "fp2"),
        },
    )
    with TraceStore.open_existing(db) as store:
        return db, build_from_traces(store)


def report(db: Path, snap: Snapshot) -> SourceReport:
    with TraceStore.open_existing(db) as store:
        return check_sources(snap, store)


def sql(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def claim_id(snap: Snapshot, name: str) -> str:
    return next(n.id for n in snap.nodes if n.name == name)


def test_an_untouched_database_checks_out(world: tuple[Path, Snapshot]) -> None:
    db, snap = world
    rep = report(db, snap)
    assert rep.all_ok and rep.diagnostics == ()
    assert set(rep.statuses) == {n.id for n in snap.nodes}
    assert rep.claim_support[claim_id(snap, f"{A} depends_on {B}")] == 2


def test_a_deleted_run_marks_its_elements_and_keeps_claims_other_runs_support(
    world: tuple[Path, Snapshot],
) -> None:
    db, snap = world
    sql(
        db,
        "DELETE FROM attempts WHERE run_id = 'r1'",
        "DELETE FROM steps WHERE run_id = 'r1'",
        "DELETE FROM runs WHERE run_id = 'r1'",
    )
    rep = report(db, snap)
    s = rep.statuses
    assert s["run:r1"] is SourceStatus.MISSING
    assert s["step:r1/s1"] is SourceStatus.MISSING
    assert s["attempt:r1/s1/1"] is SourceStatus.MISSING
    assert s["run:r2"] is SourceStatus.OK
    ab, ac = claim_id(snap, f"{A} depends_on {B}"), claim_id(snap, f"{A} depends_on {C}")
    assert s[ab] is SourceStatus.OK and rep.claim_support[ab] == 1  # r2 still states it
    assert s[ac] is SourceStatus.MISSING and rep.claim_support[ac] == 0
    assert s[f"target:{C}"] is SourceStatus.MISSING
    assert s[f"target:{A}"] is SourceStatus.OK
    assert not rep.all_ok
    codes = sorted((d.code, d.node_id) for d in rep.diagnostics)
    assert codes == [
        (DiagnosticCode.SOURCE_MISSING, "attempt:r1/s1/1"),
        (DiagnosticCode.SOURCE_MISSING, "run:r1"),
    ]


def test_an_edited_result_marks_the_attempt_changed(world: tuple[Path, Snapshot]) -> None:
    db, snap = world
    sql(
        db,
        "UPDATE attempts SET result_json = replace(result_json, 'module:c.py', 'module:zzz.py')"
        " WHERE run_id = 'r1'",
    )
    rep = report(db, snap)
    assert rep.statuses["attempt:r1/s1/1"] is SourceStatus.CHANGED
    assert rep.statuses["run:r1"] is SourceStatus.OK  # the run itself is still there
    assert rep.statuses[claim_id(snap, f"{A} depends_on {C}")] is SourceStatus.CHANGED
    assert [d.code for d in rep.diagnostics] == [DiagnosticCode.SOURCE_CHANGED]


def test_a_malformed_run_is_unreadable_not_missing(world: tuple[Path, Snapshot]) -> None:
    db, snap = world
    sql(db, "UPDATE runs SET status = 'failed' WHERE run_id = 'r2'")  # disagrees with its steps
    rep = report(db, snap)
    assert rep.statuses["run:r2"] is SourceStatus.UNREADABLE
    assert rep.statuses["attempt:r2/s1/1"] is SourceStatus.UNREADABLE
    assert rep.statuses["run:r1"] is SourceStatus.OK


def test_a_run_that_is_no_longer_finished_is_changed(
    world: tuple[Path, Snapshot], tmp_path: Path
) -> None:
    _, snap = world
    other = tmp_path / "other"
    other.mkdir()
    db = other / "traces.sqlite3"
    add_run(db, "r1", [deps_step("r1", "s1", A, [B, C])], finish=False)  # same id, never finished
    add_run(db, "r2", [deps_step("r2", "s1", A, [B])])
    rep = report(db, snap)
    assert rep.statuses["run:r1"] is SourceStatus.CHANGED
    assert rep.statuses["attempt:r1/s1/1"] is SourceStatus.MISSING
    assert rep.statuses["run:r2"] is SourceStatus.OK


def test_diagnostics_never_carry_stored_values(world: tuple[Path, Snapshot]) -> None:
    db, snap = world
    sql(db, "UPDATE attempts SET result_json = replace(result_json, 'module:b.py', 'SECRET-VALUE')")
    rep = report(db, snap)
    assert rep.diagnostics
    assert all("SECRET-VALUE" not in d.model_dump_json() for d in rep.diagnostics)
