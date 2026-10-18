"""Outcome evidence (ADR-0018): the append-only store, the outcome rules, and the claim statuses."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.experience import ExperienceGraph, Snapshot, build_from_traces, check_sources
from cognitive_mesh.experience.assess import (
    ClaimAssessment,
    ClaimStatus,
    OutcomeState,
    assess_claims,
    outcome_state,
)
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource, Holding, Rederivation
from cognitive_mesh.experience.outcomes import (
    Outcome,
    OutcomeResult,
    OutcomeSource,
    OutcomeStore,
    OutcomeStoreError,
)
from cognitive_mesh.experience.reviews import ReviewStore
from cognitive_mesh.traces import TraceStore
from experience_support import Step, deps_step, make_db

A, B = "module:a.py", "module:b.py"
FP1, FP2 = "1" * 64, "2" * 64
AB = claim_id("depends_on", A, B, "imports")
T0 = datetime(2026, 10, 18, tzinfo=UTC)


def outcome(
    source: OutcomeSource, result: OutcomeResult, fp: str = FP1, claim: str = AB, n: int = 1
) -> Outcome:
    return Outcome(
        id=n,
        claim_id=claim,
        fingerprint=fp,
        source=source,
        result=result,
        detail="d",
        recorder="r",
        previous_id=None,
        created_at=T0,
    )


# -- the store ----------------------------------------------------------------------------------


def test_add_then_list_round_trips_every_field(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.db"
    with OutcomeStore.create(path) as store:
        added = store.add(
            AB, FP1, OutcomeSource.CHECK, OutcomeResult.FAILED, "pytest -q", "ci", now=T0
        )
    with OutcomeStore.open_existing(path) as store:
        assert store.list_outcomes() == [added]
    assert added.previous_id is None and added.detail == "pytest -q"


def test_outcomes_survive_closing_and_reopening_the_file(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.db"
    with OutcomeStore.create(path) as store:
        store.add(AB, FP1, OutcomeSource.PERSON, OutcomeResult.SUCCEEDED, "checked by hand", "ann")
    with OutcomeStore.open_existing(path) as store:
        [got] = store.list_outcomes(AB)
    assert got.source is OutcomeSource.PERSON and got.recorder == "ann"


def test_each_row_links_to_the_previous_row_for_the_same_claim_and_version(tmp_path: Path) -> None:
    with OutcomeStore.create(tmp_path / "o.db") as store:
        first = store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.FAILED, "t", "ci")
        second = store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.SUCCEEDED, "t", "ci")
        other_version = store.add(AB, FP2, OutcomeSource.CHECK, OutcomeResult.FAILED, "t", "ci")
    assert second.previous_id == first.id  # history is a chain, not overwritten
    assert other_version.previous_id is None  # another code version starts its own chain


def test_a_refused_promotion_is_recorded_but_only_as_rejected(tmp_path: Path) -> None:
    with OutcomeStore.create(tmp_path / "o.db") as store:
        row = store.add(AB, FP1, OutcomeSource.PROMOTION, OutcomeResult.REJECTED, "no check", "ann")
        assert row.result is OutcomeResult.REJECTED
        with pytest.raises(OutcomeStoreError, match="only a refused promotion"):
            store.add(AB, FP1, OutcomeSource.PROMOTION, OutcomeResult.SUCCEEDED, "x", "ann")
        with pytest.raises(OutcomeStoreError, match="only a refused promotion"):
            store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.REJECTED, "x", "ci")


def test_update_and_delete_are_refused_by_the_database(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    with OutcomeStore.create(path) as store:
        store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.FAILED, "t", "ci")
    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE outcomes SET result = 'succeeded'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM outcomes")
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("claim", "fp", "detail", "recorder", "message"),
    [
        ("module:a.py", FP1, "t", "ci", "not a claim id"),
        (AB, "xyz", "t", "ci", "not a sha256"),
        (AB, FP1, "   ", "ci", "is empty"),
        (AB, FP1, "bad\x07control", "ci", "control characters"),
        (AB, FP1, "t" * 1001, "ci", "longer than"),
        (AB, FP1, "t", "r" * 101, "longer than"),
    ],
)
def test_invalid_input_is_refused_before_anything_is_written(
    tmp_path: Path, claim: str, fp: str, detail: str, recorder: str, message: str
) -> None:
    with OutcomeStore.create(tmp_path / "o.db") as store:
        with pytest.raises(OutcomeStoreError, match=message):
            store.add(claim, fp, OutcomeSource.CHECK, OutcomeResult.FAILED, detail, recorder)
        assert store.list_outcomes() == []


def test_a_secret_in_a_check_command_is_redacted_before_storing(tmp_path: Path) -> None:
    with OutcomeStore.create(tmp_path / "o.db") as store:
        row = store.add(
            AB,
            FP1,
            OutcomeSource.CHECK,
            OutcomeResult.FAILED,
            "API_TOKEN=abcdef1234567890abcdef1234567890 pytest",
            "ci",
        )
    assert "abcdef1234567890abcdef1234567890" not in row.detail


def test_read_only_open_never_creates_or_writes(tmp_path: Path) -> None:
    with pytest.raises(OutcomeStoreError, match="does not exist"):
        OutcomeStore.open_existing(tmp_path / "missing.db")
    path = tmp_path / "o.db"
    OutcomeStore.create(path).close()
    with (
        OutcomeStore.open_existing(path) as store,
        pytest.raises(OutcomeStoreError, match="read-only"),
    ):
        store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.FAILED, "t", "ci")


def test_a_reviews_file_is_not_an_outcomes_file(tmp_path: Path) -> None:
    path = tmp_path / "reviews.db"
    ReviewStore.create(path).close()
    with pytest.raises(OutcomeStoreError, match="not an outcomes file"):
        OutcomeStore.create(path)


def test_a_newer_or_unknown_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    OutcomeStore.create(path).close()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA user_version = 9")
    conn.close()
    with pytest.raises(OutcomeStoreError, match="newer than this program"):
        OutcomeStore.open_existing(path)


def test_a_malformed_stored_row_is_an_error_not_a_skip(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    with OutcomeStore.create(path) as store:
        store.add(AB, FP1, OutcomeSource.CHECK, OutcomeResult.FAILED, "t", "ci")
    conn = sqlite3.connect(
        str(path), isolation_level=None
    )  # autocommit: close() must not roll back
    conn.execute("DROP TRIGGER outcomes_no_update")
    conn.execute("UPDATE outcomes SET created_at = 'not a date'")
    conn.close()
    with (
        OutcomeStore.open_existing(path) as store,
        pytest.raises(OutcomeStoreError, match="malformed"),
    ):
        store.list_outcomes()


# -- the outcome rules --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], OutcomeState.NONE),
        ([(OutcomeSource.CHECK, OutcomeResult.SUCCEEDED)], OutcomeState.SUCCEEDED),
        ([(OutcomeSource.CHECK, OutcomeResult.FAILED)], OutcomeState.FAILED),
        ([(OutcomeSource.CHECK, OutcomeResult.AMBIGUOUS)], OutcomeState.AMBIGUOUS),
        ([(OutcomeSource.PERSON, OutcomeResult.REJECTED)], OutcomeState.NONE),
        (
            [
                (OutcomeSource.CHECK, OutcomeResult.SUCCEEDED),
                (OutcomeSource.PERSON, OutcomeResult.FAILED),
            ],
            OutcomeState.CONFLICT,
        ),
        (
            [
                (OutcomeSource.CHECK, OutcomeResult.SUCCEEDED),
                (OutcomeSource.PERSON, OutcomeResult.AMBIGUOUS),
            ],
            OutcomeState.SUCCEEDED,  # an ambiguous result never cancels a success
        ),
        (
            [
                (OutcomeSource.CHECK, OutcomeResult.FAILED),
                (OutcomeSource.CHECK, OutcomeResult.SUCCEEDED),
            ],
            OutcomeState.SUCCEEDED,  # the newest result of a source is the one that counts
        ),
        (
            [
                (OutcomeSource.CHECK, OutcomeResult.SUCCEEDED),
                (OutcomeSource.CHECK, OutcomeResult.FAILED),
            ],
            OutcomeState.FAILED,
        ),
    ],
)
def test_outcome_state_follows_the_newest_result_per_source(
    rows: list[tuple[OutcomeSource, OutcomeResult]], expected: OutcomeState
) -> None:
    got = outcome_state(outcome(src, res, n=i) for i, (src, res) in enumerate(rows, start=1))
    assert got is expected


# -- the statuses -------------------------------------------------------------------------------


def _world(tmp_path: Path, fp: str | None = FP1) -> tuple[Path, Snapshot]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runs: dict[str, tuple[list[Step], str | None]] = {"r1": ([deps_step("r1", "s1", A, [B])], fp)}
    db = make_db(tmp_path, runs)
    with TraceStore.open_existing(db) as store:
        return db, build_from_traces(store)


class _Neutral(CurrentSource):
    """A current source that neither holds nor refutes the claim, so only outcomes decide."""

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        return Rederivation(holding=Holding.SUBJECT_ABSENT)


def _assess(
    db: Path, snap: Snapshot, outcomes: list[Outcome], current: CurrentSource | None
) -> ClaimAssessment:
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    got: dict[str, ClaimAssessment] = assess_claims(
        ExperienceGraph(snap), report, [], current, outcomes
    )
    return got[AB]


def test_a_failed_check_on_the_current_version_makes_the_claim_contested(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    failed = [outcome(OutcomeSource.CHECK, OutcomeResult.FAILED)]
    got = _assess(db, snap, failed, _Neutral(FP1))
    assert got.status is ClaimStatus.CONTESTED
    assert got.outcome is OutcomeState.FAILED
    assert got.outcomes == tuple(failed)  # the history is still there


def test_a_recorded_success_validates_the_current_version(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    got = _assess(db, snap, [outcome(OutcomeSource.CHECK, OutcomeResult.SUCCEEDED)], _Neutral(FP1))
    assert got.status is ClaimStatus.VALIDATED and got.validated_by == ("outcome",)


def test_an_ambiguous_outcome_makes_the_claim_unknown_not_stronger(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    got = _assess(db, snap, [outcome(OutcomeSource.CHECK, OutcomeResult.AMBIGUOUS)], _Neutral(FP1))
    assert got.status is ClaimStatus.UNKNOWN and got.validated_by == ()


def test_a_person_and_a_check_that_disagree_are_conflicting(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    both = [
        outcome(OutcomeSource.CHECK, OutcomeResult.SUCCEEDED, n=1),
        outcome(OutcomeSource.PERSON, OutcomeResult.FAILED, n=2),
    ]
    got = _assess(db, snap, both, _Neutral(FP1))
    assert got.status is ClaimStatus.CONFLICTING and got.outcome is OutcomeState.CONFLICT


def test_an_outcome_on_another_version_does_not_change_the_status(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    got = _assess(
        db, snap, [outcome(OutcomeSource.CHECK, OutcomeResult.FAILED, fp=FP2)], _Neutral(FP1)
    )
    assert got.status is ClaimStatus.SUPPORTED and got.outcome is OutcomeState.NONE
    assert len(got.outcomes) == 1  # shown in the history, not applied


def test_without_a_root_outcomes_are_shown_but_do_not_decide(tmp_path: Path) -> None:
    db, snap = _world(tmp_path)
    got = _assess(db, snap, [outcome(OutcomeSource.CHECK, OutcomeResult.FAILED)], None)
    assert got.status is ClaimStatus.SUPPORTED and got.outcome is OutcomeState.NONE
