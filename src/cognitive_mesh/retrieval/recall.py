"""Evidence-aware recall (ADR-0016): vector hits, followed into the experience graph.

The vector search only decides which experiences are looked at. Everything said about a claim
comes from the graph, the trace database, the reviews and the current source, and similarity never
changes a status. No model provider is involved.
"""

from datetime import date
from typing import Protocol

from cognitive_mesh.experience.assess import (
    ClaimAssessment,
    assess_claims,
    attempts_behind,
    claims_of_subjects,
)
from cognitive_mesh.experience.current import CurrentSource
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import EdgeKind, Snapshot, SourceStatus
from cognitive_mesh.experience.outcomes import Outcome
from cognitive_mesh.experience.reviews import Review
from cognitive_mesh.experience.verify import SourceReport, check_attempts
from cognitive_mesh.memory.models import MemoryHit, SearchResult
from cognitive_mesh.retrieval.models import (
    RawExperience,
    RecalledClaim,
    RecallResult,
    RetrievedVia,
    Unconfirmed,
)
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.validity import Environment

NOT_IN_GRAPH = "not_in_graph"


class Searcher(Protocol):
    def search(
        self, query: str, store: TraceStore, *, k: int, min_similarity: float
    ) -> SearchResult: ...


def _raw(hit: MemoryHit) -> RawExperience:
    kind = (
        "failed"
        if hit.result_status == "failure"
        else "model_unverified"
        if hit.basis == "model_unverified"
        else "no_claims"
    )
    return RawExperience(
        run_id=hit.run_id,
        step_id=hit.step_id,
        attempt_no=hit.attempt_no,
        capability=hit.capability,
        result_status=hit.result_status,
        basis=hit.basis,
        kind=kind,
        similarity=hit.similarity,
        excerpt=hit.excerpt,
    )


def recall(
    query: str,
    searcher: Searcher,
    store: TraceStore,
    snapshot: Snapshot,
    *,
    reviews: list[Review] | None = None,
    outcomes: list[Outcome] | None = None,
    current: CurrentSource | None = None,
    k: int,
    min_similarity: float,
    today: date | None = None,
    environment: Environment | None = None,
) -> RecallResult:
    found = searcher.search(query, store, k=k, min_similarity=min_similarity)
    graph = ExperienceGraph(snapshot)
    # ADR-0019: only the hits' attempts, the claims about their subjects and the attempts those
    # claims are judged by are checked and assessed. Any other claim could not be reported anyway.
    hit_attempts = [
        f"attempt:{h.run_id}/{h.step_id}/{h.attempt_no}"
        for h in found.hits
        if graph.has_node(f"attempt:{h.run_id}/{h.step_id}/{h.attempt_no}")
    ]
    hit_claims = {
        e.source for a in hit_attempts for e in graph.in_edges(a, {EdgeKind.EVIDENCED_BY})
    }
    subjects = {graph.node(c).subject for c in hit_claims} - {None}
    scoped = claims_of_subjects(graph, {s for s in subjects if s is not None})
    statuses = check_attempts(graph, store, {*hit_attempts, *attempts_behind(graph, scoped)})
    report = SourceReport(statuses=statuses, claim_support={}, diagnostics=())
    assessments: dict[str, ClaimAssessment] = assess_claims(
        graph,
        report,
        reviews or [],
        current,
        outcomes or [],
        only={c.id for c in scoped},
        today=today,
        environment=environment,
    )

    via: dict[str, list[RetrievedVia]] = {}
    raw: list[RawExperience] = []
    unconfirmed = [
        Unconfirmed(
            run_id=u.run_id,
            step_id=u.step_id,
            attempt_no=u.attempt_no,
            similarity=u.similarity,
            reason=u.reason.value,
        )
        for u in found.unverified
    ]
    for hit in found.hits:
        doc_id = f"{hit.run_id}/{hit.step_id}/{hit.attempt_no}"
        node_id = f"attempt:{doc_id}"
        reason: str | None = None
        if not graph.has_node(node_id):
            reason = NOT_IN_GRAPH
        elif report.statuses[node_id] is not SourceStatus.OK:
            reason = report.statuses[node_id].value
        if reason is not None:
            unconfirmed.append(
                Unconfirmed(
                    run_id=hit.run_id,
                    step_id=hit.step_id,
                    attempt_no=hit.attempt_no,
                    similarity=hit.similarity,
                    reason=reason,
                )
            )
            continue
        claim_ids = [e.source for e in graph.in_edges(node_id, {EdgeKind.EVIDENCED_BY})]
        if not claim_ids:
            raw.append(_raw(hit))
        for claim_id in claim_ids:
            via.setdefault(claim_id, []).append(
                RetrievedVia(doc_id=doc_id, similarity=hit.similarity)
            )

    claims = tuple(
        RecalledClaim(
            assessment=assessments[cid],
            retrieved_via=tuple(sorted(hits, key=lambda v: (-v.similarity, v.doc_id))),
        )
        for cid, hits in sorted(via.items(), key=lambda i: (-max(v.similarity for v in i[1]), i[0]))
    )
    message = ""
    if not claims and not raw and not unconfirmed:
        message = found.message or "no sufficiently similar experience"
    elif not claims and not raw:
        message = "no similar experience with a confirmed source"
    return RecallResult(
        query_length=found.query_length,
        k=found.k,
        min_similarity=found.min_similarity,
        indexed_documents=found.indexed_documents,
        dropped_weak=found.dropped_weak,
        current_fingerprint=current.fingerprint if current else None,
        subjects_followed=len(subjects),
        claims_assessed=len(assessments),
        claims=claims,
        experiences_without_claims=tuple(raw),
        unconfirmed=tuple(unconfirmed),
        message=message,
    )
