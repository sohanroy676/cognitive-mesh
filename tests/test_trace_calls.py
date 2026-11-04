"""Model call records: attribution to attempts, storage in the trace store, migration (ADR-0038)."""

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from cognitive_mesh.agents import AgentContext, default_registry
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import Orchestrator, TaskPlan, plan_task
from cognitive_mesh.providers import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.recording import CallLog, CallRecord, CallResult, RecordingClient
from cognitive_mesh.traces import TraceRecorder, TraceStore, TraceStoreError
from cognitive_mesh.traces import store as store_module

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = '{"summary": "a project file", "symbols": []}'


class ScriptedClient:
    """Plays a list of outcomes: a ModelResponse, a ProviderError, or any other exception."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)

    def complete(self, request: ModelRequest) -> ModelResponse:
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ModelResponse)
        return outcome


def _ok(prompt: int | None = 10, completion: int | None = 4) -> ModelResponse:
    return ModelResponse(
        provider="fake",
        model="fake-1",
        text=GOOD,
        prompt_tokens=prompt,
        completion_tokens=completion,
    )


def _clock(ticks: list[float] | None) -> Callable[[], float]:
    if ticks is None:
        return time.monotonic
    values = iter(ticks)
    return lambda: next(values)


def _run(
    client: ScriptedClient, db: Path, ticks: list[float] | None = None
) -> tuple[TraceStore, str, CallLog]:
    log = CallLog()
    registry = default_registry(RecordingClient(client, log, "entry1", clock=_clock(ticks)))
    plan = TaskPlan.model_validate_json((FIXTURES / "plans" / "needs_model.json").read_bytes())
    validated = plan_task(plan, registry)
    ingestion = ingest_application(FIXTURES / "sample_app")
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    run = Orchestrator(registry, on_attempt=log.begin_attempt).run(validated, context)
    store = TraceStore.create(db)
    recorder = TraceRecorder(store)
    run_id = recorder.begin(validated, ingestion)
    recorder.finish(run_id, run, log.records)
    return store, run_id, log


def test_a_successful_call_is_recorded_with_tokens_and_latency(tmp_path: Path) -> None:
    store, _, log = _run(ScriptedClient(_ok()), tmp_path / "t.sqlite3", [1.0, 1.25])
    with store:
        listing = store.call_rows()
    assert listing.malformed_runs == () and (listing.runs_total, listing.runs_with_calls) == (1, 1)
    (row,) = listing.rows
    call = row.call
    assert call.agent_task_id == "explain.explain.1" and call.step_id == "explain"
    assert (call.call_no, call.entry_id) == (1, "entry1")
    assert (call.provider, call.model) == ("fake", "fake-1")
    assert (call.role, call.task_type) == ("code_explainer", "explain_file")
    assert (call.latency_ms, call.prompt_tokens, call.completion_tokens) == (250, 10, 4)
    assert call.result is CallResult.OK
    assert row.step_status is not None
    assert (row.step_status.value, row.attempt_status) == ("succeeded", "success")
    assert log.dropped == 0


def test_missing_token_counts_stay_unknown_not_zero(tmp_path: Path) -> None:
    store, _, _ = _run(ScriptedClient(_ok(None, None)), tmp_path / "t.sqlite3")
    with store:
        (row,) = store.call_rows().rows
    assert row.call.prompt_tokens is None and row.call.completion_tokens is None


def test_a_retried_failure_leaves_one_record_per_call_with_the_right_kind(tmp_path: Path) -> None:
    rate = ProviderError(ProviderErrorKind.RATE_LIMITED, "429")
    store, _, _ = _run(ScriptedClient(rate, _ok()), tmp_path / "t.sqlite3")
    with store:
        rows = store.call_rows().rows
    assert [(r.call.agent_task_id, r.call.result) for r in rows] == [
        ("explain.explain.1", CallResult.RATE_LIMITED),
        ("explain.explain.2", CallResult.OK),
    ]
    assert rows[0].attempt_status == "failure" and rows[1].attempt_status == "success"
    assert rows[0].call.prompt_tokens is None


def test_a_non_provider_exception_is_recorded_as_client_error_and_still_raised() -> None:
    log = CallLog()
    log.begin_attempt("t.s.1", "s", "code_explainer", "explain_file")
    client = RecordingClient(ScriptedClient(RuntimeError("boom")), log)
    with pytest.raises(RuntimeError):
        client.complete(ModelRequest(system="s", prompt="p", timeout_s=1, max_output_chars=10))
    assert [r.result for r in log.records] == [CallResult.CLIENT_ERROR]


def test_a_call_outside_any_attempt_is_not_recorded() -> None:
    log = CallLog()
    client = RecordingClient(ScriptedClient(_ok()), log)
    client.complete(ModelRequest(system="s", prompt="p", timeout_s=1, max_output_chars=10))
    assert log.records == ()


def test_records_hold_no_prompt_reply_or_error_text(tmp_path: Path) -> None:
    err = ProviderError(ProviderErrorKind.OTHER, "CANARY-ERROR-TEXT")
    store, _, log = _run(ScriptedClient(err), tmp_path / "t.sqlite3")
    store.close()
    assert "CANARY-ERROR-TEXT" not in repr(log.records)
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    try:
        text = repr(conn.execute("SELECT * FROM model_calls").fetchall())
    finally:
        conn.close()
    assert "CANARY-ERROR-TEXT" not in text and GOOD not in text and "pyproject" not in text


def test_a_failure_record_cannot_claim_token_counts() -> None:
    with pytest.raises(ValueError):
        CallRecord(
            agent_task_id="t.s.1",
            step_id="s",
            call_no=1,
            provider="p",
            model="m",
            role="r",
            task_type="c",
            latency_ms=1,
            prompt_tokens=5,
            result=CallResult.TIMEOUT,
        )


def test_records_persist_across_processes(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    store, _, _ = _run(ScriptedClient(_ok()), db)
    store.close()
    with TraceStore.open_existing(db) as reopened:
        assert len(reopened.call_rows().rows) == 1


def test_malformed_and_contradicting_rows_are_reported_not_dropped(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    store, run_id, _ = _run(ScriptedClient(_ok()), db)
    store.close()
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE model_calls SET result = 'excellent'")
        conn.commit()
    finally:
        conn.close()
    with TraceStore.open_existing(db) as reopened:
        listing = reopened.call_rows()
    assert listing.rows == () and listing.malformed_runs == (run_id,)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE model_calls SET result = 'ok', agent_task_id = 'other.explain.1'")
        conn.commit()
    finally:
        conn.close()
    with TraceStore.open_existing(db) as reopened:
        assert reopened.call_rows().malformed_runs == (run_id,)


def test_the_database_refuses_negative_tokens_and_unknown_steps(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    store, run_id, _ = _run(ScriptedClient(_ok()), db)
    store.close()
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE model_calls SET prompt_tokens = -1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE model_calls SET step_id = 'nope' WHERE run_id = ?", (run_id,))
    finally:
        conn.close()


def test_a_version_4_database_is_upgraded_and_keeps_its_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.sqlite3"
    four = {k: v for k, v in store_module.MIGRATIONS.items() if k < 4}
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "MIGRATIONS", four)
        store = TraceStore.create(db)
        plan = TaskPlan.model_validate_json((FIXTURES / "plans" / "ok.json").read_bytes())
        TraceRecorder(store).begin(plan_task(plan, default_registry()))
        store.close()
    conn = sqlite3.connect(db)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 4
    finally:
        conn.close()
    with pytest.raises(TraceStoreError, match="older version"):
        TraceStore.open_existing(db)
    with TraceStore.create(db) as upgraded:
        listing = upgraded.call_rows()
    assert (listing.runs_total, listing.runs_with_calls, listing.rows) == (1, 0, ())
