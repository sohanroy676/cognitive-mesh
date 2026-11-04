"""Decision status rules (ADR-0039), pure and through the view builder."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cognitive_mesh.decisions.models import Decision, DecisionBasis, MadeBy
from cognitive_mesh.experience import ExperienceGraph, build_from_traces
from cognitive_mesh.experience.assess import OutcomeState
from cognitive_mesh.experience.decision_assess import (
    CitationState,
    DecisionStatus,
    assess_decisions,
)
from cognitive_mesh.experience.decision_view import assess_all
from cognitive_mesh.experience.outcomes import Outcome, OutcomeResult, OutcomeSource
from cognitive_mesh.traces import TraceStore
from decisions_support import make_app, make_db, person
from experience_support import deps_step
from experience_support import make_db as make_experience_db

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def outcome(
    decision_id: str,
    result: OutcomeResult,
    fingerprint: str,
    source: OutcomeSource = OutcomeSource.CHECK,
    n: int = 1,
    valid_until: date | None = None,
) -> Outcome:
    return Outcome(
        id=n,
        claim_id=decision_id,
        fingerprint=fingerprint,
        source=source,
        result=result,
        detail="d",
        recorder="r",
        previous_id=None,
        created_at=NOW,
        valid_until=valid_until,
    )


def status_of(store: TraceStore, decision: Decision, **kwargs: object) -> DecisionStatus:
    view = assess_all(store, **kwargs)  # type: ignore[arg-type]
    return view.assessments[decision.decision_id].status


# -- files ------------------------------------------------------------------------------------


def test_an_unchanged_cited_file_is_supported_and_a_changed_one_makes_it_stale(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["b.py"], root=app)
        assert status_of(store, d, root=app) is DecisionStatus.SUPPORTED
        (app / "b.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
        view = assess_all(store, root=app)
        a = view.assessments[d.decision_id]
        assert a.status is DecisionStatus.STALE
        assert a.citations[0].state is CitationState.CHANGED
        assert any("b.py" in reason for reason in a.reasons)


def test_a_cited_file_that_disappears_makes_it_unsupported(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["b.py"], root=app)
        (app / "b.py").unlink()
        assert status_of(store, d, root=app) is DecisionStatus.UNSUPPORTED


def test_without_a_root_file_citations_are_unknown_and_the_status_says_so(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["b.py"], root=app)
        a = assess_all(store).assessments[d.decision_id]
    assert a.status is DecisionStatus.SUPPORTED
    assert a.citations[0].state is CitationState.UNKNOWN
    assert any("could not be checked" in r for r in a.reasons)
    assert a.current_fingerprint is None


def test_a_decision_that_cites_nothing_is_unsupported(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        d = person(store)
        assert status_of(store, d) is DecisionStatus.UNSUPPORTED
        assert assess_all(store).assessments[d.decision_id].reasons == ("it cites no evidence",)


def test_reasoning_text_that_mentions_evidence_is_not_evidence(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        d = person(store, reasoning="Verified in b.py and by claim:" + "a" * 32 + ". Trust me.")
        assert status_of(store, d) is DecisionStatus.UNSUPPORTED


# -- nodes and claims -----------------------------------------------------------------------


def _world(tmp_path: Path) -> tuple[Path, ExperienceGraph, str]:
    db = make_experience_db(
        tmp_path, {"run-1": ([deps_step("run-1", "s1", "module:a.py", ["module:b.py"])], None)}
    )
    with TraceStore.open_existing(db) as store:
        graph = ExperienceGraph(build_from_traces(store))
    claim = next(n.id for n in graph.claims())
    return db, graph, claim


def test_a_cited_claim_in_the_graph_supports_and_a_missing_node_does_not(tmp_path: Path) -> None:
    db, graph, claim = _world(tmp_path)
    with TraceStore.create(db) as store:
        good = person(store, cite_nodes=[claim])
        missing = person(store, summary="other", cite_nodes=["claim:" + "9" * 32])
        view = assess_all(store, graph=graph)
    assert view.assessments[good.decision_id].status is DecisionStatus.SUPPORTED
    assert view.assessments[good.decision_id].citations[0].cited_status is not None
    gone = view.assessments[missing.decision_id]
    assert gone.status is DecisionStatus.UNSUPPORTED
    assert gone.citations[0].state is CitationState.ABSENT


def test_without_a_graph_node_citations_are_unknown(tmp_path: Path) -> None:
    db, _, claim = _world(tmp_path)
    with TraceStore.create(db) as store:
        d = person(store, cite_nodes=[claim])
        a = assess_all(store).assessments[d.decision_id]
    assert a.citations[0].state is CitationState.UNKNOWN
    assert a.status is DecisionStatus.SUPPORTED


def test_a_cited_claim_that_went_stale_makes_the_decision_stale(tmp_path: Path) -> None:
    db = make_experience_db(
        tmp_path,
        {"run-1": ([deps_step("run-1", "s1", "module:a.py", ["module:b.py"])], "1" * 64)},
    )
    with TraceStore.open_existing(db) as store:
        graph = ExperienceGraph(build_from_traces(store))
    claim = next(n.id for n in graph.claims())
    app = make_app(tmp_path / "app", {"a.py": "import c\n", "c.py": "x = 1\n"})
    with TraceStore.create(db) as store:
        d = person(store, cite_nodes=[claim])
        before = assess_all(store, graph=graph)
        after = assess_all(store, graph=graph, root=app)
    assert before.assessments[d.decision_id].status is DecisionStatus.SUPPORTED
    a = after.assessments[d.decision_id]
    assert a.citations[0].cited_status == "stale"
    assert a.citations[0].state is CitationState.WEAK
    assert a.status is DecisionStatus.STALE


# -- supersession ---------------------------------------------------------------------------


def test_a_superseded_decision_stays_as_history_and_the_successor_stands(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        first = person(store, cite_files=["a.py"], root=app)
        second = person(
            store, summary="Inline it", cite_files=["a.py"], root=app, supersedes=first.decision_id
        )
        view = assess_all(store, root=app)
    one, two = view.assessments[first.decision_id], view.assessments[second.decision_id]
    assert one.status is DecisionStatus.SUPERSEDED
    assert one.superseded_by == (second.decision_id,)
    assert two.status is DecisionStatus.SUPPORTED


def test_two_decisions_replacing_the_same_one_conflict_on_both_sides(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        base = person(store, cite_files=["a.py"], root=app)
        left = person(
            store, summary="left", cite_files=["a.py"], root=app, supersedes=base.decision_id
        )
        right = person(
            store, summary="right", cite_files=["a.py"], root=app, supersedes=base.decision_id
        )
        view = assess_all(store, root=app)
    assert view.assessments[base.decision_id].status is DecisionStatus.SUPERSEDED
    a, b = view.assessments[left.decision_id], view.assessments[right.decision_id]
    assert a.status is b.status is DecisionStatus.CONFLICTING
    assert a.competing == (right.decision_id,)
    assert b.competing == (left.decision_id,)


def test_superseding_one_branch_of_a_fork_ends_the_conflict_for_the_other(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        base = person(store, cite_files=["a.py"], root=app)
        left = person(
            store, summary="left", cite_files=["a.py"], root=app, supersedes=base.decision_id
        )
        right = person(
            store, summary="right", cite_files=["a.py"], root=app, supersedes=base.decision_id
        )
        fix = person(
            store, summary="fix", cite_files=["a.py"], root=app, supersedes=left.decision_id
        )
        view = assess_all(store, root=app)
    assert view.assessments[left.decision_id].status is DecisionStatus.SUPERSEDED
    assert view.assessments[right.decision_id].status is DecisionStatus.SUPPORTED
    assert view.assessments[fix.decision_id].status is DecisionStatus.SUPPORTED


def test_a_decision_citing_a_stale_decision_is_stale(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        first = person(store, cite_files=["b.py"], root=app)
        second = person(
            store,
            summary="builds on it",
            cite_files=["a.py"],
            cite_decisions=[first.decision_id],
            root=app,
        )
        (app / "b.py").write_text("changed = True\n", encoding="utf-8")
        view = assess_all(store, root=app)
    assert view.assessments[first.decision_id].status is DecisionStatus.STALE
    assert view.assessments[second.decision_id].status is DecisionStatus.STALE


# -- outcomes -------------------------------------------------------------------------------


def _with_outcomes(
    tmp_path: Path, make: "list[tuple[OutcomeResult, OutcomeSource, bool]]"
) -> tuple[DecisionStatus, OutcomeState]:
    """One decision citing a file; outcomes (result, source, on_current_version) in order."""
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["a.py"], root=app)
        fp = d.fingerprint
        assert fp is not None
        rows = [
            outcome(d.decision_id, result, fp if current else "f" * 64, source, n)
            for n, (result, source, current) in enumerate(make, start=1)
        ]
        a = assess_all(store, root=app, outcomes=rows).assessments[d.decision_id]
    return a.status, a.outcome


@pytest.mark.parametrize(
    ("rows", "status", "state"),
    [
        ([], DecisionStatus.SUPPORTED, OutcomeState.NONE),
        (
            [(OutcomeResult.SUCCEEDED, OutcomeSource.CHECK, True)],
            DecisionStatus.VALIDATED,
            OutcomeState.SUCCEEDED,
        ),
        (
            [(OutcomeResult.SUCCEEDED, OutcomeSource.CHECK, False)],
            DecisionStatus.SUPPORTED,
            OutcomeState.NONE,
        ),
        (
            [(OutcomeResult.FAILED, OutcomeSource.PERSON, True)],
            DecisionStatus.CONTESTED,
            OutcomeState.FAILED,
        ),
        (
            [(OutcomeResult.AMBIGUOUS, OutcomeSource.PERSON, True)],
            DecisionStatus.UNKNOWN,
            OutcomeState.AMBIGUOUS,
        ),
        (
            [
                (OutcomeResult.SUCCEEDED, OutcomeSource.CHECK, True),
                (OutcomeResult.FAILED, OutcomeSource.PERSON, True),
            ],
            DecisionStatus.CONFLICTING,
            OutcomeState.CONFLICT,
        ),
    ],
)
def test_outcomes_for_the_current_code_version_decide(
    tmp_path: Path,
    rows: list[tuple[OutcomeResult, OutcomeSource, bool]],
    status: DecisionStatus,
    state: OutcomeState,
) -> None:
    assert _with_outcomes(tmp_path, rows) == (status, state)


def test_an_expired_outcome_is_ignored(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["a.py"], root=app)
        assert d.fingerprint is not None
        old = outcome(
            d.decision_id, OutcomeResult.SUCCEEDED, d.fingerprint, valid_until=date(2026, 1, 1)
        )
        a = assess_all(store, root=app, outcomes=[old], today=date(2026, 11, 4))
    assert a.assessments[d.decision_id].status is DecisionStatus.SUPPORTED


def test_a_failed_outcome_wins_over_a_changed_file(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["b.py"], root=app)
        (app / "b.py").write_text("changed = True\n", encoding="utf-8")
        from cognitive_mesh.decisions.record import observe_files

        fp = observe_files(app).fingerprint
        rows = [outcome(d.decision_id, OutcomeResult.FAILED, fp)]
        assert (
            assess_all(store, root=app, outcomes=rows).assessments[d.decision_id].status
            is DecisionStatus.CONTESTED
        )


def test_without_a_root_no_outcome_counts_and_the_status_says_so(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["a.py"], root=app)
        assert d.fingerprint is not None
        rows = [outcome(d.decision_id, OutcomeResult.SUCCEEDED, d.fingerprint)]
        a = assess_all(store, outcomes=rows).assessments[d.decision_id]
    assert a.status is DecisionStatus.SUPPORTED
    assert any("can be matched to the code" in r for r in a.reasons)


# -- model-written decisions ----------------------------------------------------------------


def test_a_model_decision_recorded_many_times_is_never_validated_by_repetition(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    model = {
        "made_by": MadeBy.MODEL,
        "recorder": None,
        "provider": "stub",
        "model": "m1",
        "role": "planner",
    }
    with TraceStore.create(db) as store:
        made = [
            person(store, cite_files=["a.py"], root=app, run_id="run-1", step_id="s1", **model)
            for _ in range(5)
        ]
        view = assess_all(store, root=app)
    for d in made:
        assert d.basis is DecisionBasis.MODEL_UNVERIFIED
        a = view.assessments[d.decision_id]
        # the run it was made in succeeded; that is not an outcome of the decision
        assert a.status is DecisionStatus.SUPPORTED
        assert a.outcome is OutcomeState.NONE
        assert any("written by a model" in r for r in a.reasons)


def test_pure_rules_ignore_outcomes_of_other_ids() -> None:
    assert assess_decisions([]) == {}
