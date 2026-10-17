"""Claim status rules (ADR-0016): one case per status, plus the adversarial ones."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.experience import ExperienceGraph, Snapshot, build_from_traces, check_sources
from cognitive_mesh.experience.assess import (
    ClaimAssessment,
    ClaimStatus,
    assess_claims,
    orphaned_reviews,
)
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource, Holding, Rederivation
from cognitive_mesh.experience.reviews import Review, Verdict
from cognitive_mesh.traces import TraceStore
from experience_support import Step, deps_step, make_db, unresolved_step

A, B, C = "module:a.py", "module:b.py", "module:c.py"
FP1, FP2, FP3 = "1" * 64, "2" * 64, "3" * 64
AB = claim_id("depends_on", A, B, "imports")
AC = claim_id("depends_on", A, C, "imports")
T0 = datetime(2026, 10, 17, tzinfo=UTC)


class StubCurrent(CurrentSource):
    """The current source with a fixed answer; the real derivation is tested elsewhere."""

    def __init__(self, fingerprint: str, holding: Holding) -> None:
        self.fingerprint = fingerprint
        self._holding = holding

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        return Rederivation(holding=self._holding)


def review(verdict: Verdict, fp: str, claim: str = AB, n: int = 1) -> Review:
    return Review(
        id=n,
        claim_id=claim,
        fingerprint=fp,
        verdict=verdict,
        note="n",
        reviewer="r",
        created_at=T0,
    )


def world(
    tmp_path: Path, runs: Mapping[str, tuple[list[Step], str | None]]
) -> tuple[Path, Snapshot]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, runs)
    with TraceStore.open_existing(db) as store:
        return db, build_from_traces(store)


def assess(
    db: Path,
    snap: Snapshot,
    *,
    reviews: list[Review] | None = None,
    current: CurrentSource | None = None,
) -> dict[str, ClaimAssessment]:
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    return assess_claims(ExperienceGraph(snap), report, reviews or [], current)


def one(tmp_path: Path, fp: str | None = FP1) -> tuple[Path, Snapshot]:
    runs: dict[str, tuple[list[Step], str | None]] = {"r1": ([deps_step("r1", "s1", A, [B])], fp)}
    return world(tmp_path, runs)


# -- the status table ---------------------------------------------------------------------------


def test_supported_without_a_root_says_currency_is_unknown(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap)[AB]
    assert got.status is ClaimStatus.SUPPORTED
    assert got.current_fingerprint is None and got.validated_by == ()
    assert got.counts.supporting_ok == 1 and got.counts.distinct_fingerprints == 1
    assert "unknown" in got.meaning


def test_repetition_does_not_change_the_status(tmp_path: Path) -> None:
    many = {f"r{i}": ([deps_step(f"r{i}", "s1", A, [B])], FP1) for i in range(5)}
    one_db, one_snap = one(tmp_path / "one")
    many_db, many_snap = world(tmp_path / "many", many)
    single, repeated = assess(one_db, one_snap)[AB], assess(many_db, many_snap)[AB]
    assert single.status is repeated.status is ClaimStatus.SUPPORTED
    assert repeated.counts.supporting_ok == 5 and repeated.counts.distinct_fingerprints == 1
    for current in (None, StubCurrent(FP2, Holding.SUBJECT_ABSENT)):
        a = assess(one_db, one_snap, current=current)[AB].status
        b = assess(many_db, many_snap, current=current)[AB].status
        assert a is b


def test_validated_by_rederivation_on_the_current_version(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, current=StubCurrent(FP1, Holding.HOLDS))[AB]
    assert got.status is ClaimStatus.VALIDATED and got.validated_by == ("rederived",)
    assert got.current_fingerprint == FP1


def test_validated_by_a_confirming_review_on_the_current_version(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    current = StubCurrent(FP1, Holding.SUBJECT_ABSENT)
    got = assess(db, snap, current=current, reviews=[review(Verdict.CONFIRMED, FP1)])[AB]
    assert got.status is ClaimStatus.VALIDATED and got.validated_by == ("reviewed",)


def test_a_review_of_another_version_does_not_validate(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    current = StubCurrent(FP2, Holding.SUBJECT_ABSENT)
    got = assess(db, snap, current=current, reviews=[review(Verdict.CONFIRMED, FP1)])[AB]
    assert got.status is ClaimStatus.STALE
    assert got.counts.confirmed_other == 1 and got.counts.confirmed_current == 0


def test_a_review_without_a_root_does_not_validate(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, reviews=[review(Verdict.CONFIRMED, FP1)])[AB]
    assert got.status is ClaimStatus.SUPPORTED and got.validated_by == ()


@pytest.mark.parametrize("holding", [Holding.SUBJECT_ABSENT, Holding.REFUTED])
def test_stale_when_only_other_versions_observed_it(tmp_path: Path, holding: Holding) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, current=StubCurrent(FP2, holding))[AB]
    assert got.status is ClaimStatus.STALE  # a refutation on another version is not a conflict


def test_unsupported_when_no_observation_traces_back_to_its_source(tmp_path: Path) -> None:
    _, snap = one(tmp_path / "a")
    other_db, _ = world(tmp_path / "b", {"zz": ([deps_step("zz", "s1", B, [C])], FP1)})
    got = assess(other_db, snap)[AB]  # the trace database no longer holds run r1
    assert got.status is ClaimStatus.UNSUPPORTED
    assert got.counts.supporting_ok == 0 and got.counts.supporting_not_ok == 1


def test_unsupported_beats_a_stale_or_supported_reading(tmp_path: Path) -> None:
    _, snap = one(tmp_path / "a")
    other_db, _ = world(tmp_path / "b", {"zz": ([deps_step("zz", "s1", B, [C])], FP1)})
    got = assess(other_db, snap, current=StubCurrent(FP2, Holding.SUBJECT_ABSENT))[AB]
    assert got.status is ClaimStatus.UNSUPPORTED


# -- conflicts ----------------------------------------------------------------------------------


def test_same_version_contradiction_is_a_conflict_with_both_sides_listed(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C])], FP1),
        },
    )
    got = assess(db, snap)
    assert got[AB].status is ClaimStatus.CONFLICTING
    assert [s.run_id for s in got[AB].supporting] == ["r1"]
    assert [c.run_id for c in got[AB].contradicting] == ["r2"]
    assert got[AB].contradicting[0].same_version and got[AB].counts.contradicting_same_version == 1
    assert got[AC].status is ClaimStatus.CONFLICTING  # and the other way round: no winner


def test_cross_version_difference_is_shown_but_is_not_a_conflict(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C])], FP2),
        },
    )
    got = assess(db, snap)[AB]
    assert got.status is ClaimStatus.SUPPORTED and got.differs_across_versions
    assert not got.contradicting[0].same_version and got.contradicting[0].fingerprint == FP2
    now_fp1 = assess(db, snap, current=StubCurrent(FP1, Holding.HOLDS))[AB]
    assert now_fp1.status is ClaimStatus.VALIDATED and now_fp1.differs_across_versions
    now_fp2 = assess(db, snap, current=StubCurrent(FP2, Holding.REFUTED))[AB]
    assert now_fp2.status is ClaimStatus.STALE


def test_a_truncated_listing_never_contradicts(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C], truncated=True)], FP1),
        },
    )
    got = assess(db, snap)[AB]
    assert got.status is ClaimStatus.SUPPORTED and got.contradicting == ()


def test_a_listing_about_another_subject_never_contradicts(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", C, [A])], FP1),
        },
    )
    assert assess(db, snap)[AB].contradicting == ()


def test_unrecorded_versions_cannot_be_the_same_version(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], None),
            "r2": ([deps_step("r2", "s1", A, [C])], None),
        },
    )
    got = assess(db, snap)[AB]
    assert got.status is ClaimStatus.SUPPORTED and got.differs_across_versions
    assert got.counts.distinct_fingerprints == 0


def test_a_contradicting_observation_whose_source_is_gone_does_not_count(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C])], FP1),
        },
    )
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs WHERE run_id = 'r2'")
    conn.commit()
    conn.close()
    got = assess(db, snap)[AB]
    assert got.contradicting == () and got.status is ClaimStatus.SUPPORTED


def test_rejected_review_against_supported_version_is_a_conflict(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, reviews=[review(Verdict.REJECTED, FP1)])[AB]
    assert got.status is ClaimStatus.CONFLICTING
    assert got.supporting and got.reviews[0].verdict is Verdict.REJECTED


def test_a_rejection_of_another_version_is_not_a_conflict(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, reviews=[review(Verdict.REJECTED, FP2)])[AB]
    assert got.status is ClaimStatus.SUPPORTED and got.counts.rejected_other == 1


def test_confirmed_and_rejected_on_the_same_version_conflict(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    both = [review(Verdict.CONFIRMED, FP3, n=1), review(Verdict.REJECTED, FP3, n=2)]
    assert assess(db, snap, reviews=both)[AB].status is ClaimStatus.CONFLICTING


def test_refuted_on_the_version_that_was_observed_is_a_conflict(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    got = assess(db, snap, current=StubCurrent(FP1, Holding.REFUTED))[AB]
    assert got.status is ClaimStatus.CONFLICTING and got.validated_by == ()


def test_holding_now_against_a_rejection_is_a_conflict_not_a_validation(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    current = StubCurrent(FP1, Holding.HOLDS)
    got = assess(db, snap, current=current, reviews=[review(Verdict.REJECTED, FP1)])[AB]
    assert got.status is ClaimStatus.CONFLICTING


def test_unresolved_claims_are_contradicted_by_a_complete_report_without_them(
    tmp_path: Path,
) -> None:
    x = "unresolved:import:python:yaml"
    db, snap = world(
        tmp_path,
        {
            "r1": ([unresolved_step("r1", "s1", [x])], FP1),
            "r2": ([unresolved_step("r2", "s1", [])], FP1),
        },
    )
    cid = claim_id("is_unresolved", x, "", "")
    got = assess(db, snap)[cid]
    assert got.status is ClaimStatus.CONFLICTING and [c.run_id for c in got.contradicting] == ["r2"]


def test_an_unrelated_claim_is_unaffected_by_a_review_of_another(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B, C])], FP1)})
    got = assess(db, snap, reviews=[review(Verdict.REJECTED, FP1, claim=AC)])
    assert got[AB].status is ClaimStatus.SUPPORTED and got[AC].status is ClaimStatus.CONFLICTING


def test_reviews_of_claims_not_in_the_graph_are_reported_as_orphaned(tmp_path: Path) -> None:
    db, snap = one(tmp_path)
    gone = "claim:" + "0" * 32
    reviews = [review(Verdict.CONFIRMED, FP1), review(Verdict.CONFIRMED, FP1, claim=gone, n=2)]
    assert [r.claim_id for r in orphaned_reviews(ExperienceGraph(snap), reviews)] == [gone]
    assert set(assess(db, snap, reviews=reviews)) == {AB}
