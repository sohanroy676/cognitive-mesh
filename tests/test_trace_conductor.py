"""Run-level conductor calls and decisions written with the run: trace schema 7 (ADR-0040)."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognitive_mesh.agents import AgentContext, default_registry
from cognitive_mesh.decisions.models import Decision, DecisionError, MadeBy
from cognitive_mesh.decisions.record import prepare_decision
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import Orchestrator, TaskPlan, TaskRun, plan_task
from cognitive_mesh.providers import ModelRequest
from cognitive_mesh.providers.recording import CallLog, CallRecord, CallResult, RecordingClient
from cognitive_mesh.traces import SCHEMA_VERSION, TraceRecorder, TraceStore
from cognitive_mesh.traces import store as store_module
from conductor_support import ScriptedClient, response
from test_trace_calls import GOOD, _ok, _run
from test_trace_calls import ScriptedClient as CallsClient

FIXTURES = Path(__file__).parent / "fixtures"
REQUEST = ModelRequest(system="s", prompt="p", timeout_s=5.0, max_output_chars=100)


def sql(db: Path, statement: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


def _conducted_run(db: Path) -> tuple[TraceStore, TraceRecorder, str, TaskRun, CallLog]:
    """A finished-able run whose log holds a conductor call and the step's own call."""
    log = CallLog()
    log.begin_conductor("explain", 1)
    RecordingClient(ScriptedClient(response("{}")), log, "alpha").complete(REQUEST)
    registry = default_registry(RecordingClient(ScriptedClient(response(GOOD)), log, "beta"))
    plan = TaskPlan.model_validate_json((FIXTURES / "plans" / "needs_model.json").read_bytes())
    validated = plan_task(plan, registry)
    ingestion = ingest_application(FIXTURES / "sample_app")
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    run = Orchestrator(registry, on_attempt=log.begin_attempt).run(validated, context)
    store = TraceStore.create(db)
    recorder = TraceRecorder(store)
    run_id = recorder.begin(validated, ingestion)
    return store, recorder, run_id, run, log


def _decision(
    run_id: str,
    *,
    step_id: str | None = None,
    cites: tuple[str, ...] = (),
    agent_task_id: str | None = "explain._conductor.1",
) -> Decision:
    return prepare_decision(
        summary="Assign beta",
        options=["alpha", "beta"],
        chosen="beta",
        reasoning="because",
        made_by=MadeBy.MODEL,
        provider="fake",
        model="fake-1",
        role="conductor",
        entry_id="alpha",
        agent_task_id=agent_task_id,
        run_id=run_id,
        step_id=step_id,
        cite_decisions=list(cites),
    )


def test_a_conductor_call_has_no_step_and_reads_back_beside_the_step_calls(tmp_path: Path) -> None:
    store, recorder, run_id, run, log = _conducted_run(tmp_path / "t.sqlite3")
    with store:
        recorder.finish(run_id, run, log.records)
        listing = store.call_rows()
    assert listing.malformed_runs == ()
    by_role = {r.call.role: r for r in listing.rows}
    conductor = by_role["conductor"]
    assert conductor.call.step_id is None and conductor.step_status is None
    assert (conductor.call.agent_task_id, conductor.call.task_type) == (
        "explain._conductor.1",
        "conduct",
    )
    assert by_role["code_explainer"].call.step_id == "explain"
    assert by_role["code_explainer"].step_status is not None


@pytest.mark.parametrize(("step_id", "role"), [(None, "code_explainer"), ("explain", "conductor")])
def test_only_a_conductor_call_has_no_step(step_id: str | None, role: str) -> None:
    with pytest.raises(ValidationError, match="only a conductor call has no step"):
        CallRecord(
            agent_task_id="t.x.1",
            step_id=step_id,
            call_no=1,
            provider="p",
            model="m",
            role=role,
            task_type="conduct",
            latency_ms=1,
            result=CallResult.OK,
        )


def test_a_conductor_call_filed_under_another_prefix_makes_the_run_malformed(
    tmp_path: Path,
) -> None:
    store, recorder, run_id, run, log = _conducted_run(tmp_path / "t.sqlite3")
    with store:
        recorder.finish(run_id, run, log.records)
        store._conn.execute(
            "UPDATE model_calls SET agent_task_id = 'other._conductor.1' WHERE step_id IS NULL"
        )
        store._conn.commit()
        listing = store.call_rows()
    assert listing.malformed_runs == (run_id,)


def test_migration_6_to_7_keeps_old_calls_and_allows_run_level_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(
            store_module, "MIGRATIONS", {k: v for k, v in store_module.MIGRATIONS.items() if k != 6}
        )
        old_store, _, _ = _run(CallsClient(_ok()), db)
        old_store.close()
    assert sql(db, "PRAGMA user_version") == [(6,)]
    before = sql(db, "SELECT * FROM model_calls")
    assert before
    info = {r[1]: r[3] for r in sql(db, "PRAGMA table_info(model_calls)")}
    assert info["step_id"] == 1  # NOT NULL before
    TraceStore.create(db).close()
    assert sql(db, "PRAGMA user_version") == [(SCHEMA_VERSION,)] == [(7,)]
    assert sql(db, "SELECT * FROM model_calls") == before
    info = {r[1]: r[3] for r in sql(db, "PRAGMA table_info(model_calls)")}
    assert info["step_id"] == 0  # nullable now
    with TraceStore.open_existing(db) as store:  # still reads
        assert len(store.call_rows().rows) == len(before)


def test_decisions_are_written_in_the_same_transaction_as_the_run(tmp_path: Path) -> None:
    store, recorder, run_id, run, log = _conducted_run(tmp_path / "t.sqlite3")
    plan = _decision(run_id)
    step = _decision(run_id, step_id="explain", cites=(plan.decision_id,))
    with store:
        recorder.finish(run_id, run, log.records, [plan, step])
        stored = store.read_decisions()
    assert stored.malformed == ()
    assert {d.step_id for d in stored.decisions} == {None, "explain"}


def test_a_decision_that_cannot_be_stored_rolls_back_the_whole_finish(tmp_path: Path) -> None:
    store, recorder, run_id, run, log = _conducted_run(tmp_path / "t.sqlite3")
    plan = _decision(run_id)
    step = _decision(run_id, step_id="explain", cites=(plan.decision_id,))
    with store:
        # the step decision comes first, so the decision it cites does not exist yet
        with pytest.raises(DecisionError, match="does not exist"):
            recorder.finish(run_id, run, log.records, [step, plan])
        trace = store.get(run_id)
        assert trace.status.value == "interrupted" and trace.steps == ()
        assert store.call_rows().rows == () and store.read_decisions().decisions == ()
        # the run is still open, so a correct finish works afterwards
        recorder.finish(run_id, run, log.records, [plan, step])
        assert len(store.read_decisions().decisions) == 2


def test_a_decision_naming_a_call_that_was_not_recorded_rolls_back(tmp_path: Path) -> None:
    store, recorder, run_id, run, log = _conducted_run(tmp_path / "t.sqlite3")
    lost = prepare_decision(
        summary="s",
        options=["a"],
        chosen="a",
        reasoning="r",
        made_by=MadeBy.MODEL,
        provider="fake",
        model="fake-1",
        role="conductor",
        agent_task_id="explain._conductor.9",
        run_id=run_id,
    )
    with store:
        with pytest.raises(DecisionError, match="model call of the decision was not recorded"):
            recorder.finish(run_id, run, log.records, [lost])
        assert store.get(run_id).status.value == "interrupted"


def test_a_decision_may_name_a_run_without_a_step_but_a_step_needs_its_run(
    tmp_path: Path,
) -> None:
    store, _, run_id, _, _ = _conducted_run(tmp_path / "t.sqlite3")
    with store:
        store.add_decision(_decision(run_id, agent_task_id=None))
        (stored,) = store.read_decisions().decisions
        assert stored.step_id is None and stored.run_id == run_id
        with pytest.raises(DecisionError, match="run of the decision does not exist"):
            store.add_decision(_decision("no-such-run", agent_task_id=None))
    with pytest.raises(ValidationError, match="a step needs its run_id"):
        Decision.model_validate(
            {**_decision(run_id).model_dump(mode="json"), "run_id": None, "step_id": "explain"}
        )
