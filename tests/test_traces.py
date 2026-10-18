"""Trace store tests (ADR-0011): persistence, status honesty, malformed records, redaction."""

import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from cognitive_mesh.agents import (
    Agent,
    AgentContext,
    AgentFailure,
    AgentRegistry,
    AgentResult,
    AgentSuccess,
    AgentTask,
    Basis,
    Capability,
    FailureKind,
    Handled,
    ModelInfo,
)
from cognitive_mesh.cli import (
    EXIT_OK,
    EXIT_PLAN_ERROR,
    EXIT_TASK_NOT_SUCCEEDED,
    EXIT_TRACE_NOT_FOUND,
    EXIT_TRACE_STORE_ERROR,
    main,
)
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import (
    Orchestrator,
    PlanStep,
    RetryPolicy,
    StepStatus,
    TaskPlan,
    TaskRun,
    TaskStatus,
    ValidatedPlan,
    plan_task,
)
from cognitive_mesh.traces import (
    SCHEMA_VERSION,
    RunTrace,
    StepTrace,
    TraceNotFoundError,
    TraceRecorder,
    TraceRecordError,
    TraceStatus,
    TraceStore,
    TraceStoreError,
    source_fingerprint,
)

FIXTURES = Path(__file__).parent / "fixtures"
PLANS = FIXTURES / "plans"
DEP_APP = FIXTURES / "dep_app"
SAMPLE_APP = FIXTURES / "sample_app"
CTX = AgentContext()
T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def fake_secret() -> str:
    return "gsk_" + "a1B2c3D4" * 4  # assembled at runtime; matches the groq_key rule


class _Empty(BaseModel):
    pass


class _Note(BaseModel):
    note: str | None = None


class Fake(Agent):
    """An agent driven by a script: (task, call number) -> result or exception."""

    def __init__(self, agent_id: str, script: Callable[[AgentTask, int], object]) -> None:
        self.id = agent_id
        self.role = "test"
        self.description = "scripted test agent"
        self.script = script
        self.calls = 0
        super().__init__(
            [
                Capability(
                    name="work",
                    description="test",
                    input_model=_Note,
                    output_model=_Empty,
                    handler=lambda i, ctx: Handled(_Empty()),
                )
            ]
        )

    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        self.calls += 1
        outcome = self.script(task, self.calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


def good(task: AgentTask, **kw: object) -> AgentSuccess:
    return AgentSuccess(
        agent_id="a",
        task_id=task.task_id,
        capability=task.capability,
        output=kw.get("output", {}),
        basis=kw.get("basis", Basis.DETERMINISTIC),
        model=kw.get("model"),
    )


def bad(
    task: AgentTask, *, retryable: bool = False, message: str = "provider down"
) -> AgentFailure:
    return AgentFailure(
        agent_id="a",
        task_id=task.task_id,
        capability=task.capability,
        kind=FailureKind.PROVIDER_UNAVAILABLE,
        message=message,
        retryable=retryable,
    )


def step(step_id: str, *deps: str, **kw: object) -> PlanStep:
    return PlanStep(step_id=step_id, capability="work", depends_on=deps, **kw)


def ticking(offset: int = 0) -> Callable[[], datetime]:
    state = {"n": offset}

    def now() -> datetime:
        state["n"] += 1
        return T0 + timedelta(seconds=state["n"])

    return now


def execute(
    script: Callable[[AgentTask, int], object],
    *steps: PlanStep,
    policy: RetryPolicy | None = None,
    with_clock: bool = True,
    **kw: object,
) -> tuple[ValidatedPlan, TaskRun]:
    registry = AgentRegistry()
    registry.register(Fake("a", script))
    plan = plan_task(TaskPlan(task_id="t", steps=steps), registry)
    orch = Orchestrator(registry, policy, now=ticking() if with_clock else None, **kw)  # type: ignore[arg-type]
    return plan, orch.run(plan, CTX)


def record(
    db: Path,
    plan: ValidatedPlan,
    run: TaskRun,
    run_id: str = "run-1",
    ingestion: object = None,
    offset: int = 0,
) -> str:
    with TraceStore.create(db) as store:
        recorder = TraceRecorder(store, now=ticking(offset), new_run_id=lambda: run_id)
        rid = recorder.begin(plan, ingestion)  # type: ignore[arg-type]
        recorder.finish(rid, run)
        return rid


def sql(db: Path, statement: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(statement, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def read(db: Path, run_id: str = "run-1") -> RunTrace:
    with TraceStore.open_existing(db) as store:
        return store.get(run_id)


# ---- recording and reading back ---------------------------------------------------------------


def test_successful_run_round_trips_with_task_step_and_agent_association(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("first"), step("second", "first"))
    db = tmp_path / "t.sqlite3"
    assert record(db, plan, run) == "run-1"
    with TraceStore.open_existing(db) as store:
        trace = store.get("run-1")
    assert trace.status is TraceStatus.SUCCEEDED
    assert trace.task_id == "t" and trace.finished_at is not None
    assert trace.to_task_run() == run
    assert [s.step_id for s in trace.steps] == ["first", "second"]
    first = trace.steps[0]
    assert first.agent_id == "a" and first.attempts[0].agent_task_id == "t.first.1"
    assert first.attempts[0].result.task_id == "t.first.1"
    assert first.started_at is not None and first.finished_at is not None


def test_failed_run_keeps_failure_details_and_is_never_succeeded(tmp_path: Path) -> None:
    def script(t: AgentTask, n: int) -> object:
        return bad(t)

    plan, run = execute(script, step("boom"), step("after", "boom"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    trace = read(db)
    assert trace.status is TraceStatus.FAILED
    boom, after = trace.steps
    assert (boom.status, boom.failure_kind, boom.message) == (
        StepStatus.FAILED,
        "provider_unavailable",
        "provider down",
    )
    assert isinstance(boom.attempts[0].result, AgentFailure)
    assert (after.status, after.blocked_by, after.attempts) == (StepStatus.BLOCKED, ("boom",), ())


def test_agent_exception_and_invalid_result_are_stored_as_failed(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: RuntimeError("secret-ish"), step("x"))
    record(tmp_path / "t.sqlite3", plan, run)
    trace = read(tmp_path / "t.sqlite3")
    assert trace.status is TraceStatus.FAILED
    assert trace.steps[0].failure_kind == "agent_raised"
    assert trace.steps[0].attempt_count == 1 and trace.steps[0].attempts == ()


def test_retried_step_stores_every_attempt_with_its_own_task_id(tmp_path: Path) -> None:
    def script(t: AgentTask, n: int) -> object:
        return bad(t, retryable=True) if n < 3 else good(t)

    plan, run = execute(script, step("flaky"), policy=RetryPolicy(max_attempts=3))
    record(tmp_path / "t.sqlite3", plan, run)
    trace = read(tmp_path / "t.sqlite3")
    flaky = trace.steps[0]
    assert trace.status is TraceStatus.SUCCEEDED
    assert [a.agent_task_id for a in flaky.attempts] == ["t.flaky.1", "t.flaky.2", "t.flaky.3"]
    assert [a.result.status for a in flaky.attempts] == ["failure", "failure", "success"]


def test_budget_exhausted_run_is_stored_incomplete_with_not_run_steps(tmp_path: Path) -> None:
    clock = {"t": 0.0}

    def script(t: AgentTask, n: int) -> object:
        clock["t"] += 10
        return good(t)

    plan, run = execute(
        script,
        step("a1"),
        step("a2", "a1"),
        step("a3", "a2"),
        run_budget_s=15,
        clock=lambda: clock["t"],
    )
    record(tmp_path / "t.sqlite3", plan, run)
    trace = read(tmp_path / "t.sqlite3")
    assert trace.status is TraceStatus.INCOMPLETE
    assert [s.status for s in trace.steps][-1] is StepStatus.NOT_RUN
    assert trace.to_task_run().status is TaskStatus.INCOMPLETE


def test_model_info_basis_and_evidence_survive(tmp_path: Path) -> None:
    out: dict[str, object] = {"text": "hello", "n": [1, 2, {"k": None}]}
    info = ModelInfo(provider="groq", model="m-1")
    plan, run = execute(
        lambda t, n: good(t, output=out, basis=Basis.MODEL_UNVERIFIED, model=info), step("x")
    )
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    result = read(db).steps[0].attempts[0].result
    assert isinstance(result, AgentSuccess)
    assert result.basis is Basis.MODEL_UNVERIFIED and result.model == info and result.output == out
    assert sql(db, "SELECT model_provider, model_name, basis FROM attempts") == [
        ("groq", "m-1", "model_unverified")
    ]


def test_optional_fields_may_be_missing(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"), with_clock=False)
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)  # no ingestion, no step timestamps
    trace = read(db)
    assert trace.source_root is None and trace.source_fingerprint is None
    assert trace.steps[0].started_at is None
    assert trace.status is TraceStatus.SUCCEEDED


def test_source_and_environment_are_recorded(tmp_path: Path) -> None:
    ingestion = ingest_application(DEP_APP)
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run, ingestion=ingestion)
    trace = read(db)
    assert trace.source_fingerprint == source_fingerprint(ingestion)
    assert trace.source_root == ingestion.root
    assert trace.environment.python_version.startswith("3.12")
    assert source_fingerprint(ingest_application(SAMPLE_APP)) != source_fingerprint(ingestion)
    assert source_fingerprint(ingest_application(DEP_APP)) == source_fingerprint(ingestion)


def test_each_run_has_its_own_stable_id_and_listing_is_newest_first(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run, run_id="r-a-old")
    record(db, plan, run, run_id="r-b-new", offset=1000)
    with TraceStore.open_existing(db) as store:
        listing = store.list()
        assert store.get("r-a-old").run_id == "r-a-old"
    assert [r.run_id for r in listing.runs] == ["r-b-new", "r-a-old"]
    assert listing.malformed == ()


def test_unknown_run_id_raises_not_found(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    with TraceStore.open_existing(db) as store, pytest.raises(TraceNotFoundError):
        store.get("nope")


# ---- persistence across sessions --------------------------------------------------------------


def test_traces_survive_closing_and_reopening_the_store(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: bad(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    first = read(db)
    second = read(db)  # a brand-new connection
    assert first == second and first.status is TraceStatus.FAILED
    with TraceStore.create(db) as store:  # reopening for writing keeps old data
        assert store.get("run-1") == first


def test_a_separate_process_reads_a_trace_written_by_this_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    code = main(["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--trace-db", str(db)])
    assert code == EXIT_OK
    trace_id = next(
        line.split(": ")[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("trace:")
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cognitive_mesh", "traces", "show", trace_id, "--db", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert f"run {trace_id}: succeeded" in result.stdout
    assert "report" in result.stdout and "dependencies_of" in result.stdout


# ---- interrupted runs and transactional writes ------------------------------------------------


def test_a_run_that_never_finished_reads_back_as_interrupted(tmp_path: Path) -> None:
    plan, _ = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        TraceRecorder(store, now=ticking(), new_run_id=lambda: "run-1").begin(plan)
    trace = read(db)
    assert trace.status is TraceStatus.INTERRUPTED
    assert trace.steps == () and trace.finished_at is None
    with TraceStore.open_existing(db) as store:
        assert [r.status for r in store.list().runs] == [TraceStatus.INTERRUPTED]


def test_a_failed_final_write_leaves_nothing_partial_and_reads_interrupted(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    with TraceStore.create(db) as store:
        recorder = TraceRecorder(store, now=ticking(), new_run_id=lambda: "run-1")
        recorder.begin(plan)
        dup = StepTrace(seq=0, step_id="x", capability="work", status=StepStatus.SUCCEEDED)
        with pytest.raises(TraceStoreError) as err:
            store.finish_run(
                "run-1", TraceStatus.SUCCEEDED, T0, [], [dup, dup.model_copy(update={"seq": 1})]
            )
        assert "nothing was written" in str(err.value)
    assert sql(db, "SELECT count(*) FROM steps") == [(0,)]
    assert sql(db, "SELECT state, status FROM runs") == [("running", None)]
    assert read(db).status is TraceStatus.INTERRUPTED


def test_a_run_cannot_be_finished_twice_or_without_a_header(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    with TraceStore.create(db) as store:
        with pytest.raises(TraceStoreError):
            store.finish_run("run-1", TraceStatus.FAILED, T0, [], [])
        with pytest.raises(TraceStoreError):
            store.finish_run("ghost", TraceStatus.FAILED, T0, [], [])
    assert read(db).status is TraceStatus.SUCCEEDED
    assert sql(db, "SELECT count(*) FROM steps") == [(1,)]


def test_finishing_with_an_unfinished_status_is_rejected(tmp_path: Path) -> None:
    plan, _ = execute(lambda t, n: good(t), step("x"))
    with TraceStore.create(tmp_path / "t.sqlite3") as store:
        TraceRecorder(store, new_run_id=lambda: "run-1").begin(plan)
        with pytest.raises(ValueError, match="finished run"):
            store.finish_run("run-1", TraceStatus.INTERRUPTED, T0, [], [])


def test_duplicate_run_id_is_a_store_error(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    with TraceStore.create(db) as store, pytest.raises(TraceStoreError):
        TraceRecorder(store, new_run_id=lambda: "run-1").begin(plan)
    assert sql(db, "SELECT count(*) FROM runs") == [(1,)]


# ---- malformed records ------------------------------------------------------------------------


def _set(table: str, assignment: str, where: str = "") -> str:
    # Fixed test strings only; no external input reaches this SQL.
    return f"UPDATE {table} SET {assignment} WHERE run_id = 'bad'{where}"  # noqa: S608


CORRUPTIONS = {
    "result_garbage": _set("attempts", "result_json = '{not json SECRETMARK'"),
    "result_shape": _set("attempts", 'result_json = \'{"status": "success"}\''),
    "run_status_lies": _set("runs", "status = 'succeeded'"),
    "step_status_lies": _set("steps", "status = 'succeeded'", " AND status = 'failed'"),
    "step_status_enum": _set("steps", "status = 'SECRETMARK'"),
    "agent_misassociated": _set("attempts", "agent_id = 'other'"),
    "task_misassociated": _set("attempts", "agent_task_id = 'x.y.1'"),
    "result_status_col": _set("attempts", "result_status = 'success'"),
    "unknown_state": _set("runs", "state = 'SECRETMARK'"),
    "running_with_data": _set("runs", "state = 'running'"),
    "unfinished_status": _set("runs", "status = 'interrupted'"),
    "null_status": _set("runs", "status = NULL"),
    "no_finish_time": _set("runs", "finished_at = NULL"),
    "plan_shape": _set("runs", "plan_json = '[]'"),
    "env_garbage": _set("runs", "env_json = 'SECRETMARK'"),
    "bad_timestamp": _set("runs", "started_at = 'SECRETMARK'"),
    "seq_gap": _set("steps", "seq = 7"),
    "too_few_attempts": _set("steps", "attempt_count = 0", " AND status = 'failed'"),
    "blocked_by_shape": _set("steps", "blocked_by_json = '{}'"),
}


@pytest.fixture
def two_runs(tmp_path: Path) -> Path:
    db = tmp_path / "t.sqlite3"
    plan, run = execute(lambda t, n: bad(t), step("boom"), step("after", "boom"))
    record(db, plan, run, run_id="bad")
    plan, run = execute(lambda t, n: good(t), step("ok"))
    record(db, plan, run, run_id="good")
    return db


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_a_corrupted_run_is_reported_not_trusted_and_does_not_hide_other_runs(
    two_runs: Path, name: str
) -> None:
    sql(two_runs, CORRUPTIONS[name])
    with TraceStore.open_existing(two_runs) as store:
        with pytest.raises(TraceRecordError) as err:
            store.get("bad")
        assert "bad" in str(err.value) and "SECRETMARK" not in str(err.value)
        listing = store.list()
        assert store.get("good").status is TraceStatus.SUCCEEDED
    assert listing.malformed == ("bad",)
    assert [r.run_id for r in listing.runs] == ["good"]


def test_a_stored_succeeded_status_cannot_hide_a_failed_step(two_runs: Path) -> None:
    sql(two_runs, "UPDATE runs SET status = 'succeeded' WHERE run_id = 'bad'")
    with TraceStore.open_existing(two_runs) as store, pytest.raises(TraceRecordError) as err:
        store.get("bad")
    assert "disagrees" in str(err.value)


# ---- database files ---------------------------------------------------------------------------


def test_readers_never_create_a_database(tmp_path: Path) -> None:
    db = tmp_path / "missing.sqlite3"
    with pytest.raises(TraceStoreError, match="does not exist"):
        TraceStore.open_existing(db)
    assert not db.exists()


def test_a_file_that_is_not_sqlite_is_rejected_and_left_untouched(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    db.write_bytes(b"this is not a database " * 200)
    for opener in (TraceStore.open_existing, TraceStore.create):
        with pytest.raises(TraceStoreError):
            opener(db)
    assert db.read_bytes() == b"this is not a database " * 200


def test_a_foreign_sqlite_database_is_not_adopted(tmp_path: Path) -> None:
    db = tmp_path / "other.sqlite3"
    sql(db, "CREATE TABLE people (name TEXT)")
    with pytest.raises(TraceStoreError, match="not a trace database"):
        TraceStore.create(db)
    with pytest.raises(TraceStoreError):
        TraceStore.open_existing(db)
    assert sql(db, "SELECT name FROM sqlite_master") == [("people",)]


def test_an_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    sql(db, f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    for opener in (TraceStore.open_existing, TraceStore.create):
        with pytest.raises(TraceStoreError, match="version"):
            opener(db)


def test_an_empty_file_becomes_a_new_trace_database_only_for_writers(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    db.write_bytes(b"")
    with pytest.raises(TraceStoreError):
        TraceStore.open_existing(db)
    with TraceStore.create(db) as store:
        assert store.list().runs == ()


def test_an_unusable_path_is_a_store_error_without_leaking_sql(tmp_path: Path) -> None:
    with pytest.raises(TraceStoreError) as err:
        TraceStore.create(tmp_path / "no-such-dir" / "t.sqlite3")
    assert "SELECT" not in str(err.value) and "INSERT" not in str(err.value)
    with pytest.raises(TraceStoreError):
        TraceStore.create(tmp_path)  # a directory


def test_readers_cannot_write(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    with TraceStore.open_existing(db) as store, pytest.raises(TraceStoreError):
        TraceRecorder(store, new_run_id=lambda: "other").begin(plan)
    assert sql(db, "SELECT count(*) FROM runs") == [(1,)]


# ---- redaction --------------------------------------------------------------------------------


def test_secrets_in_inputs_outputs_and_messages_never_reach_the_database(tmp_path: Path) -> None:
    secret = fake_secret()

    def script(t: AgentTask, n: int) -> object:
        if t.task_id.startswith("t.boom"):
            return bad(t, message=f"failed with key {secret}")
        return good(t, output={"leaked": [f"token={secret}"], "ok": "fine"})

    plan, run = execute(
        script,
        step("fine", inputs={"note": secret}),
        step("boom"),
    )
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    assert secret.encode() not in db.read_bytes()
    live = run.steps[0].attempts[0]
    assert isinstance(live, AgentSuccess) and live.output["leaked"] == [f"token={secret}"]
    trace = read(db)
    assert trace.redactions
    assert "[REDACTED:" in json.dumps(trace.plan)
    message = trace.steps[1].message
    assert message is not None and "[REDACTED:" in message
    stored = trace.steps[0].attempts[0].result
    assert isinstance(stored, AgentSuccess) and stored.output["ok"] == "fine"
    leaked = stored.output["leaked"]
    assert isinstance(leaked, list) and "[REDACTED:" in str(leaked[0])
    assert all(secret not in r for r in trace.redactions)


def test_clean_data_is_stored_unchanged_and_reports_no_redactions(tmp_path: Path) -> None:
    plan, run = execute(lambda t, n: good(t, output={"a": "plain text"}), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    trace = read(db)
    assert trace.redactions == ()
    assert trace.to_task_run() == run


# ---- orchestrator clock -----------------------------------------------------------------------


def test_step_timestamps_are_ordered_when_a_clock_is_given() -> None:
    _, run = execute(lambda t, n: good(t), step("a"), step("b", "a"))
    a, b = run.steps
    assert a.started_at and a.finished_at and b.started_at and b.finished_at
    assert a.started_at < a.finished_at <= b.started_at < b.finished_at


def test_without_a_clock_run_output_has_no_timestamps() -> None:
    _, run = execute(lambda t, n: good(t), step("a"), with_clock=False)
    assert run.steps[0].started_at is None and run.steps[0].finished_at is None


def test_blocked_and_not_run_steps_also_get_timestamps() -> None:
    _, run = execute(lambda t, n: bad(t), step("a"), step("b", "a"))
    assert run.steps[1].status is StepStatus.BLOCKED and run.steps[1].started_at is not None


# ---- CLI --------------------------------------------------------------------------------------


def test_cli_records_a_failed_run_and_exits_7(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    args = [
        "run",
        str(PLANS / "needs_model.json"),
        "--root",
        str(SAMPLE_APP),
        "--trace-db",
        str(db),
    ]
    assert main(args) == EXIT_TASK_NOT_SUCCEEDED
    out = capsys.readouterr().out
    trace_id = next(line.split(": ")[1] for line in out.splitlines() if line.startswith("trace:"))
    assert main(["traces", "show", trace_id, "--db", str(db)]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "failed" in shown and "provider_unavailable" in shown and "blocked by: explain" in shown
    assert main(["traces", "show", trace_id, "--db", str(db), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed" and payload["run_id"] == trace_id
    assert [s["status"] for s in payload["steps"]] == ["succeeded", "failed", "blocked"]


def test_cli_list_shows_runs_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "t.sqlite3"
    for _ in range(2):
        assert (
            main(["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--trace-db", str(db)])
            == 0
        )
    capsys.readouterr()
    assert main(["traces", "list", "--db", str(db)]) == EXIT_OK
    assert capsys.readouterr().out.count("task=demo") == 2
    assert main(["traces", "list", "--db", str(db), "--json"]) == EXIT_OK
    listing = json.loads(capsys.readouterr().out)
    assert len(listing["runs"]) == 2 and listing["malformed"] == []
    assert listing["runs"][0]["run_id"] != listing["runs"][1]["run_id"]


def test_cli_run_json_with_trace_has_trace_id_and_without_has_no_timestamps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--json"]
    assert main(base) == EXIT_OK
    plain = json.loads(capsys.readouterr().out)
    assert "trace_id" not in plain and "started_at" not in plain["steps"][0]
    assert main([*base, "--trace-db", str(tmp_path / "t.sqlite3")]) == EXIT_OK
    traced = json.loads(capsys.readouterr().out)
    assert traced["trace_id"] and traced["steps"][0]["started_at"]


def test_cli_unknown_run_id_exits_9(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "t.sqlite3"
    main(["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--trace-db", str(db)])
    capsys.readouterr()
    assert main(["traces", "show", "nope\x1b[31m", "--db", str(db)]) == EXIT_TRACE_NOT_FOUND
    err = capsys.readouterr().err
    assert "\x1b" not in err and "no run has the id" in err


def test_cli_missing_or_garbage_database_exits_8_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert main(["traces", "list", "--db", str(missing)]) == EXIT_TRACE_STORE_ERROR
    assert main(["traces", "show", "x", "--db", str(missing)]) == EXIT_TRACE_STORE_ERROR
    assert not missing.exists()
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"junk" * 100)
    assert main(["traces", "list", "--db", str(junk)]) == EXIT_TRACE_STORE_ERROR
    assert "SELECT" not in capsys.readouterr().err


def test_cli_list_reports_malformed_runs_and_exits_8(
    two_runs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sql(two_runs, CORRUPTIONS["run_status_lies"])
    assert main(["traces", "list", "--db", str(two_runs)]) == EXIT_TRACE_STORE_ERROR
    captured = capsys.readouterr()
    assert "good" in captured.out and "bad" in captured.err and "malformed" in captured.err
    assert main(["traces", "show", "bad", "--db", str(two_runs)]) == EXIT_TRACE_STORE_ERROR


def test_cli_invalid_plan_creates_no_database(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    code = main(["run", str(PLANS / "cycle.json"), "--root", str(DEP_APP), "--trace-db", str(db)])
    assert code == EXIT_PLAN_ERROR and not db.exists()


def test_cli_unwritable_trace_db_exits_8_before_running_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--trace-db", str(tmp_path)]
    )
    captured = capsys.readouterr()
    assert code == EXIT_TRACE_STORE_ERROR and captured.out == ""


def test_cli_show_escapes_hostile_stored_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = "\x1b[31mRED<script>"
    plan, run = execute(lambda t, n: bad(t, message=hostile), step("x"))
    db = tmp_path / "t.sqlite3"
    record(db, plan, run)
    assert main(["traces", "show", "run-1", "--db", str(db)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\\x1b[31mRED<script>" in out
    assert read(db).steps[0].message == hostile


def test_cli_prints_the_run_and_warns_when_the_final_write_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: TraceRecorder, run_id: str, run: TaskRun) -> TraceStatus:
        raise TraceStoreError("cannot finish the run (sqlite error); nothing was written")

    monkeypatch.setattr(TraceRecorder, "finish", broken)
    db = tmp_path / "t.sqlite3"
    code = main(["run", str(PLANS / "ok.json"), "--root", str(DEP_APP), "--trace-db", str(db)])
    captured = capsys.readouterr()
    assert code == EXIT_TRACE_STORE_ERROR
    assert "task demo: succeeded" in captured.out and "trace:" not in captured.out
    assert "the trace was not saved" in captured.err
    monkeypatch.undo()
    with TraceStore.open_existing(db) as store:
        assert [r.status for r in store.list().runs] == [TraceStatus.INTERRUPTED]


def test_cli_does_not_run_when_the_run_cannot_be_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(
        self: TraceRecorder, plan: ValidatedPlan, ingestion: object = None, **_: object
    ) -> str:
        raise TraceStoreError("cannot record the run (sqlite error)")

    monkeypatch.setattr(TraceRecorder, "begin", broken)
    code = main(
        [
            "run",
            str(PLANS / "ok.json"),
            "--root",
            str(DEP_APP),
            "--trace-db",
            str(tmp_path / "t.db"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_TRACE_STORE_ERROR and captured.out == ""
