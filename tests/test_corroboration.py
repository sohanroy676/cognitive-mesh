"""Corroboration and lineage (ADR-0020): independence is by method class, per code version."""

from pathlib import Path

from cognitive_mesh.experience import ExperienceGraph, check_sources
from cognitive_mesh.experience.assess import (
    ClaimAssessment,
    ClaimStatus,
    Corroboration,
    SourceClass,
    assess_claims,
)
from cognitive_mesh.experience.current import Holding
from cognitive_mesh.experience.outcomes import Outcome, OutcomeResult, OutcomeSource
from cognitive_mesh.experience.reviews import Verdict
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step
from test_experience_assess import AB, FP1, FP2, T0, A, B, StubCurrent, review, world


def outcome(source: OutcomeSource, result: OutcomeResult, fp: str = FP1, n: int = 1) -> Outcome:
    return Outcome(
        id=n,
        claim_id=AB,
        fingerprint=fp,
        source=source,
        result=result,
        detail="d",
        recorder="r",
        previous_id=None,
        created_at=T0,
    )


def runs(*fingerprints: str | None) -> dict[str, tuple[list, str | None]]:  # type: ignore[type-arg]
    return {f"r{i}": ([deps_step(f"r{i}", "s1", A, [B])], fp) for i, fp in enumerate(fingerprints)}


def got(
    tmp_path: Path,
    fps: tuple[str | None, ...],
    *,
    reviews: list | None = None,  # type: ignore[type-arg]
    outcomes: list[Outcome] | None = None,
    current: StubCurrent | None = None,
) -> ClaimAssessment:
    db, snap = world(tmp_path, runs(*fps))
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    return assess_claims(ExperienceGraph(snap), report, reviews or [], current, outcomes or [])[AB]


def classes(a: ClaimAssessment) -> set[SourceClass]:
    return {g.source_class for g in a.sources}


def test_one_run_is_a_single_source(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,))
    assert a.corroboration is Corroboration.SINGLE
    assert classes(a) == {SourceClass.ANALYZER} and a.repeats == 0


def test_five_runs_of_one_version_are_still_one_source(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,) * 5)
    assert a.corroboration is Corroboration.SINGLE
    assert len(a.sources) == 1 and len(a.sources[0].records) == 5 and a.repeats == 4
    assert (
        "5 records" in a.corroboration_reason and "1 independent source" in a.corroboration_reason
    )


def test_a_rederivation_does_not_add_an_independent_source(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,), current=StubCurrent(FP1, Holding.HOLDS))
    assert a.status is ClaimStatus.VALIDATED
    assert a.corroboration is Corroboration.SINGLE and classes(a) == {SourceClass.ANALYZER}
    assert a.repeats == 1


def test_a_person_review_on_the_same_version_corroborates(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,), reviews=[review(Verdict.CONFIRMED, FP1)])
    assert a.corroboration is Corroboration.CORROBORATED
    assert classes(a) == {SourceClass.ANALYZER, SourceClass.PERSON}


def test_many_reviews_are_one_person_source(tmp_path: Path) -> None:
    reviews = [review(Verdict.CONFIRMED, FP1, n=i) for i in (1, 2, 3)]
    a = got(tmp_path, (FP1,), reviews=reviews)
    assert classes(a) == {SourceClass.ANALYZER, SourceClass.PERSON}
    assert a.repeats == 2


def test_a_check_outcome_on_the_same_version_corroborates(tmp_path: Path) -> None:
    outcomes = [outcome(OutcomeSource.CHECK, OutcomeResult.SUCCEEDED)]
    a = got(tmp_path, (FP1,), outcomes=outcomes, current=StubCurrent(FP1, Holding.HOLDS))
    assert a.corroboration is Corroboration.CORROBORATED
    assert classes(a) == {SourceClass.ANALYZER, SourceClass.CHECK}


def test_a_review_on_another_version_does_not_corroborate(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,), reviews=[review(Verdict.CONFIRMED, FP2)])
    for version in a.by_version:
        assert version.category is not Corroboration.CORROBORATED
    assert {v.fingerprint for v in a.by_version} == {FP1, FP2}
    assert a.versions_supported == 2


def test_the_same_claim_on_two_versions_is_persistence_not_corroboration(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1, FP2))
    assert a.corroboration is Corroboration.SINGLE and a.repeats == 0
    assert a.versions_supported == 2


def test_with_a_root_the_current_version_decides(tmp_path: Path) -> None:
    current = StubCurrent(FP2, Holding.SUBJECT_ABSENT)
    a = got(tmp_path, (FP1,), current=current)
    assert a.corroboration is Corroboration.NONE
    assert a.corroboration_version == FP2


def test_a_rejecting_review_is_contradicted_and_keeps_both_sides(tmp_path: Path) -> None:
    a = got(tmp_path, (FP1,), reviews=[review(Verdict.REJECTED, FP1)])
    assert a.corroboration is Corroboration.CONTRADICTED
    assert a.contradicted_by == (SourceClass.PERSON,)
    assert classes(a) == {SourceClass.ANALYZER}  # the support is not hidden
    assert a.status is ClaimStatus.CONFLICTING


def test_a_failed_outcome_is_contradicted(tmp_path: Path) -> None:
    outcomes = [outcome(OutcomeSource.CHECK, OutcomeResult.FAILED)]
    a = got(tmp_path, (FP1,), outcomes=outcomes, current=StubCurrent(FP1, Holding.HOLDS))
    assert a.corroboration is Corroboration.CONTRADICTED
    assert a.status is ClaimStatus.CONTESTED


def test_a_run_without_a_version_has_no_lineage(tmp_path: Path) -> None:
    a = got(tmp_path, (None,))
    assert a.corroboration is Corroboration.NONE
    assert len(a.no_lineage) == 1 and "no lineage" in a.corroboration_reason


def test_promotion_and_ambiguous_outcomes_add_nothing(tmp_path: Path) -> None:
    outcomes = [
        outcome(OutcomeSource.PROMOTION, OutcomeResult.SUCCEEDED, n=1),
        outcome(OutcomeSource.CHECK, OutcomeResult.AMBIGUOUS, n=2),
    ]
    a = got(tmp_path, (FP1,), outcomes=outcomes, current=StubCurrent(FP1, Holding.SUBJECT_ABSENT))
    assert classes(a) == {SourceClass.ANALYZER}
    assert a.corroboration is Corroboration.SINGLE


def test_every_category_has_a_reason_and_the_result_is_deterministic(tmp_path: Path) -> None:
    cases = [
        got(tmp_path / "a", (None,)),
        got(tmp_path / "b", (FP1,)),
        got(tmp_path / "c", (FP1,), reviews=[review(Verdict.CONFIRMED, FP1)]),
        got(tmp_path / "d", (FP1,), reviews=[review(Verdict.REJECTED, FP1)]),
    ]
    assert {c.corroboration for c in cases} == set(Corroboration)
    assert all(c.corroboration_reason for c in cases)
    again = got(tmp_path / "e", (FP1,), reviews=[review(Verdict.CONFIRMED, FP1)])
    assert again.corroboration_reason == cases[2].corroboration_reason
