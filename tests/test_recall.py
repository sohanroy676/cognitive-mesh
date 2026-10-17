"""Evidence-aware recall (ADR-0016): the roadmap's v0.9.0 test cases, with a scripted searcher."""

from pathlib import Path

from cognitive_mesh.experience import Snapshot, build_from_traces
from cognitive_mesh.experience.assess import ClaimStatus
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource, Holding, Rederivation
from cognitive_mesh.memory.models import MemoryHit, SearchResult, SourceCheck, UnverifiedHit
from cognitive_mesh.retrieval import NOT_IN_GRAPH, RAW_LABEL, RecallResult, recall
from cognitive_mesh.traces import TraceStore
from experience_support import Step, deps_step, failed_step, make_db, model_step
from memory_support import add_run

A, B, C = "module:a.py", "module:b.py", "module:c.py"
FP1, FP2 = "1" * 64, "2" * 64
AB = claim_id("depends_on", A, B, "imports")
AC = claim_id("depends_on", A, C, "imports")


class Scripted:
    """Returns fixed hits, as the vector index would; it never looks at the query."""

    def __init__(
        self,
        hits: list[tuple[str, str, int, float]],
        unverified: list[UnverifiedHit] | None = None,
        basis: str | None = "deterministic",
        status: str = "success",
    ) -> None:
        self._hits = hits
        self._unverified = unverified or []
        self._basis, self._status = basis, status

    def search(
        self, query: str, store: TraceStore, *, k: int, min_similarity: float
    ) -> SearchResult:
        hits = tuple(
            MemoryHit(
                run_id=r,
                step_id=s,
                attempt_no=n,
                capability="dependencies_of",
                result_status=self._status,
                basis=self._basis,
                similarity=sim,
                excerpt="capability: dependencies_of",
            )
            for r, s, n, sim in self._hits
        )
        return SearchResult(
            query_length=len(query),
            k=k,
            min_similarity=min_similarity,
            hits=hits,
            unverified=tuple(self._unverified),
            dropped_weak=0,
            indexed_documents=len(hits),
            message="",
        )


class StubCurrent(CurrentSource):
    def __init__(self, fingerprint: str, holding: Holding) -> None:
        self.fingerprint = fingerprint
        self._holding = holding

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        return Rederivation(holding=self._holding)


def run_recall(
    db: Path,
    snap: Snapshot,
    searcher: Scripted,
    current: CurrentSource | None = None,
) -> RecallResult:
    with TraceStore.open_existing(db) as store:
        return recall("q", searcher, store, snap, current=current, k=5, min_similarity=0.2)


def world(tmp_path: Path, runs: dict[str, tuple[list[Step], str | None]]) -> tuple[Path, Snapshot]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, runs)
    with TraceStore.open_existing(db) as store:
        return db, build_from_traces(store)


def test_a_relevant_claim_comes_back_with_its_supporting_records(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    got = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.8)]))
    (claim,) = got.claims
    assert claim.assessment.claim_id == AB and claim.assessment.status is ClaimStatus.SUPPORTED
    assert [(s.run_id, s.doc_id, s.fingerprint) for s in claim.assessment.supporting] == [
        ("r1", "r1/s1/1", FP1)
    ]
    assert [(v.doc_id, v.similarity) for v in claim.retrieved_via] == [("r1/s1/1", 0.8)]
    assert got.experiences_without_claims == () and got.unconfirmed == ()
    assert got.current_fingerprint is None  # currency unknown without a root


def test_contradicting_evidence_is_surfaced_not_discarded(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C])], FP1),
        },
    )
    got = run_recall(db, snap, Scripted([("r2", "s1", 1, 0.9)]))  # only r2 matched the query
    (claim,) = got.claims
    assert claim.assessment.claim_id == AC
    assert claim.assessment.status is ClaimStatus.CONFLICTING
    assert [c.run_id for c in claim.assessment.contradicting] == ["r1"]  # r1 never matched


def test_a_claim_reached_by_two_hits_appears_once_with_both(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [B])], FP1),
        },
    )
    got = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.5), ("r2", "s1", 1, 0.7)]))
    (claim,) = got.claims
    assert [v.doc_id for v in claim.retrieved_via] == ["r2/s1/1", "r1/s1/1"]
    assert claim.assessment.counts.supporting_ok == 2


def test_missing_provenance_is_unconfirmed_and_never_support(tmp_path: Path) -> None:
    db, snap = world(tmp_path / "a", {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    other, _ = world(tmp_path / "b", {"zz": ([deps_step("zz", "s1", C, [A])], FP1)})
    got = run_recall(other, snap, Scripted([("r1", "s1", 1, 0.95)]))  # r1 no longer in the traces
    assert got.claims == () and got.experiences_without_claims == ()
    assert [(u.run_id, u.reason) for u in got.unconfirmed] == [("r1", "source_missing")]
    assert "confirmed source" in got.message


def test_index_level_unverified_hits_are_kept_apart(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    bad = UnverifiedHit(
        run_id="r9", step_id="s1", attempt_no=1, similarity=0.6, reason=SourceCheck.CHANGED
    )
    got = run_recall(db, snap, Scripted([], unverified=[bad]))
    assert [(u.run_id, u.reason) for u in got.unconfirmed] == [("r9", "source_changed")]
    assert got.claims == ()


def test_expired_validity_is_stale_and_still_shown(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    got = run_recall(
        db, snap, Scripted([("r1", "s1", 1, 0.8)]), StubCurrent(FP2, Holding.SUBJECT_ABSENT)
    )
    assert got.claims[0].assessment.status is ClaimStatus.STALE
    assert got.current_fingerprint == FP2
    assert got.claims[0].assessment.supporting  # kept, not deleted


def test_validated_for_the_current_version_names_its_method(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    got = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.8)]), StubCurrent(FP1, Holding.HOLDS))
    assessment = got.claims[0].assessment
    assert assessment.status is ClaimStatus.VALIDATED and assessment.validated_by == ("rederived",)


def test_similarity_never_changes_a_status(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    low = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.21)]))
    high = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.999)]))
    assert low.claims[0].assessment == high.claims[0].assessment
    assert high.claims[0].retrieved_via[0].similarity == 0.999


def test_model_text_and_failures_stay_raw_however_similar(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {"r1": ([model_step("r1", "s1"), failed_step("r1", "s2")], FP1)},
    )
    model = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.99)], basis="model_unverified"))
    assert model.claims == ()
    assert [(e.kind, e.label) for e in model.experiences_without_claims] == [
        ("model_unverified", RAW_LABEL)
    ]
    failed = run_recall(db, snap, Scripted([("r1", "s2", 1, 0.99)], basis=None, status="failure"))
    assert [e.kind for e in failed.experiences_without_claims] == ["failed"]


def test_a_run_newer_than_the_graph_is_not_in_the_graph(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    add_run(db, "r2", [deps_step("r2", "s1", A, [B])], fingerprint=FP1)
    got = run_recall(db, snap, Scripted([("r2", "s1", 1, 0.9)]))
    assert [(u.run_id, u.reason) for u in got.unconfirmed] == [("r2", NOT_IN_GRAPH)]
    assert got.claims == ()


def test_no_hits_says_so(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    got = run_recall(db, snap, Scripted([]))
    assert got.claims == () and got.message == "no sufficiently similar experience"


def test_notes_always_say_what_similarity_and_validated_do_not_mean(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    notes = " ".join(run_recall(db, snap, Scripted([])).notes)
    assert "not evidence" in notes and "not proof" in notes


def test_json_carries_every_supporting_attempt_reference(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", A, [B])], FP2),
        },
    )
    text = run_recall(db, snap, Scripted([("r1", "s1", 1, 0.5)])).model_dump_json()
    for needle in ("r1/s1/1", "r2/s1/1", FP1, FP2, "attempt:r2/s1/1"):
        assert needle in text
