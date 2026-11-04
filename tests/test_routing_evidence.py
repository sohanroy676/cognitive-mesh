"""The evidence label of an assignment: a count test, never a ranking (ADR-0040)."""

from typing import Literal

from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.orchestrator.routing_evidence import (
    INSUFFICIENT,
    RECORDED,
    SAMPLE_MINIMUM,
    EvidenceTable,
)
from cognitive_mesh.providers.recording import CallRecord, CallResult
from cognitive_mesh.traces import CallListing, CallRow

Attempt = Literal["success", "failure"] | None


def _row(
    n: int,
    attempt: Attempt,
    *,
    entry: str | None = "alpha",
    role: str = "code_explainer",
    task: str = "explain_file",
    step: str | None = "s",
    result: CallResult = CallResult.OK,
) -> CallRow:
    call = CallRecord(
        agent_task_id=f"t{n}.s.1" if step else f"t{n}._conductor.1",
        step_id=step,
        call_no=1,
        entry_id=entry,
        provider="p",
        model="m",
        role=role,
        task_type=task,
        latency_ms=5,
        result=result,
    )
    return CallRow(
        run_id=f"r{n}",
        call=call,
        step_status=StepStatus.SUCCEEDED if step else None,
        attempt_status=attempt,
    )


def _table(rows: list[CallRow]) -> EvidenceTable:
    listing = CallListing(
        rows=tuple(rows), malformed_runs=(), runs_total=len(rows), runs_with_calls=len(rows)
    )
    return EvidenceTable(listing)


def test_the_minimum_is_twenty_calls_with_a_known_outcome() -> None:
    assert SAMPLE_MINIMUM == 20
    zero = _table([]).get("alpha", "code_explainer", "explain_file")
    assert (zero.calls, zero.known_outcomes, zero.label) == (0, 0, INSUFFICIENT)
    nineteen = _table([_row(i, "success") for i in range(19)]).get(
        "alpha", "code_explainer", "explain_file"
    )
    assert (nineteen.known_outcomes, nineteen.label) == (19, INSUFFICIENT)
    twenty = _table([_row(i, "success") for i in range(20)]).get(
        "alpha", "code_explainer", "explain_file"
    )
    assert (twenty.known_outcomes, twenty.label) == (20, RECORDED)


def test_failures_count_as_known_outcomes_and_unmatched_attempts_do_not() -> None:
    rows = [_row(i, "success") for i in range(10)]
    rows += [_row(100 + i, "failure") for i in range(10)]
    rows += [_row(200 + i, None) for i in range(30)]  # no attempt row matches: outcome unknown
    ev = _table(rows).get("alpha", "code_explainer", "explain_file")
    assert (ev.calls, ev.known_outcomes, ev.attempts_success, ev.attempts_failure) == (
        50,
        20,
        10,
        10,
    )
    assert ev.label == RECORDED
    only_unknown = _table([_row(i, None) for i in range(50)]).get(
        "alpha", "code_explainer", "explain_file"
    )
    assert only_unknown.calls == 50 and only_unknown.label == INSUFFICIENT


def test_samples_never_mix_entries_roles_or_task_types() -> None:
    rows = [_row(i, "success") for i in range(15)]
    rows += [_row(100 + i, "success", entry="beta") for i in range(15)]
    rows += [_row(200 + i, "success", task="other_task") for i in range(15)]
    rows += [_row(300 + i, "success", role="dependency_analyst") for i in range(15)]
    table = _table(rows)
    assert table.get("alpha", "code_explainer", "explain_file").known_outcomes == 15
    assert table.get("beta", "code_explainer", "explain_file").known_outcomes == 15
    assert table.get("alpha", "code_explainer", "other_task").known_outcomes == 15
    assert table.get("alpha", "dependency_analyst", "explain_file").known_outcomes == 15
    assert table.get("gamma", "code_explainer", "explain_file").calls == 0


def test_the_conductors_own_calls_and_unnamed_entries_are_never_counted() -> None:
    rows = [_row(i, None, role="conductor", task="conduct", step=None) for i in range(30)] + [
        _row(100 + i, "success", entry=None) for i in range(30)
    ]
    table = _table(rows)
    assert table.get("alpha", "conductor", "conduct").calls == 0
    assert table.get("alpha", "code_explainer", "explain_file").calls == 0


def test_the_description_states_counts_not_a_ranking() -> None:
    text = (
        _table([_row(i, "success") for i in range(3)])
        .get("alpha", "code_explainer", "explain_file")
        .describe()
    )
    assert (
        text == "insufficient_evidence: 3 call(s) with a known outcome of 3 recorded (minimum 20)"
    )
    assert "best" not in text and "better" not in text


def test_a_failed_call_in_a_successful_attempt_is_a_failure_sample() -> None:
    # alpha was rate limited and beta took over inside one successful attempt (ADR-0040)
    rows = [_row(i, "success", result=CallResult.RATE_LIMITED) for i in range(20)] + [
        _row(100 + i, "success", entry="beta") for i in range(20)
    ]
    table = _table(rows)
    alpha = table.get("alpha", "code_explainer", "explain_file")
    assert (alpha.attempts_success, alpha.attempts_failure, alpha.known_outcomes) == (0, 20, 20)
    beta = table.get("beta", "code_explainer", "explain_file")
    assert (beta.attempts_success, beta.attempts_failure) == (20, 0)
