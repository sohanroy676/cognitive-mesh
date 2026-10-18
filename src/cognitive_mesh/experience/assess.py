"""Evidence status of claims (ADR-0016). Pure: no I/O, no model, no clock.

A status is a category with a fixed meaning (`STATUS_MEANING`), never a score. It is computed on
every call from what the caller passes in. Only attempts whose source still checks out (`ok`) count.
Similarity and repetition are inputs to nothing here: five identical observations and one give the
same status.

Per code version (fingerprint) F, a claim has `pro(F)`: an ok observation on F, a confirmed review
on F, or (when the current source is given and F is its fingerprint) a re-derivation that holds. It
has `con(F)`: a complete ok observation on F that lacks the claim, a rejected review on F, or a
re-derivation on F that is refuted. A claim is `conflicting` when some F has both.

Outcomes (ADR-0018) apply to the current code version only, like reviews: a recorded failure makes
the claim `contested`, an ambiguous one `unknown`, a recorded success counts as validation, and a
person and a check that disagree are `conflicting`.
"""

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.experience.current import CurrentSource, Holding, Rederivation
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import EdgeKind, ExpNode, NodeKind, SourceStatus
from cognitive_mesh.experience.outcomes import (
    EVIDENCE_RESULTS,
    Outcome,
    OutcomeResult,
    OutcomeSource,
)
from cognitive_mesh.experience.reviews import Review, Verdict
from cognitive_mesh.experience.verify import SourceReport

UNRESOLVED_CAPABILITY: Final = "capability:unresolved_report"


class ClaimStatus(StrEnum):
    CONFLICTING = "conflicting"
    CONTESTED = "contested"
    VALIDATED = "validated"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    SUPPORTED = "supported"


class OutcomeState(StrEnum):
    """What the recorded outcomes say about the current code version (ADR-0018)."""

    NONE = "none"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"  # a person and a check disagree


STATUS_MEANING: Final[dict[ClaimStatus, str]] = {
    ClaimStatus.CONFLICTING: (
        "for one code version there is both support and a contradiction (an observation that "
        "lacks the claim, a rejecting review, or a refuting re-derivation); both sides are listed"
    ),
    ClaimStatus.CONTESTED: (
        "a recorded outcome for the current code version says the work this claim supported "
        "failed; "
        "earlier states stay in the outcome history"
    ),
    ClaimStatus.VALIDATED: (
        "holds for the current code version (--root) by re-derivation, a confirming review, or a "
        "recorded successful outcome; this is not proof that the claim is true"
    ),
    ClaimStatus.UNKNOWN: (
        "a recorded outcome for the current code version is ambiguous (the check did not settle "
        "it); "
        "it neither confirms nor weakens the claim"
    ),
    ClaimStatus.UNSUPPORTED: "no recorded observation of this claim can be traced to its source",
    ClaimStatus.STALE: (
        "observed only on other code versions, and not confirmed for the current one (--root)"
    ),
    ClaimStatus.SUPPORTED: (
        "observed with traceable sources and not contradicted; whether it is still current is "
        "unknown because no --root was given"
    ),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SupportingAttempt(_Frozen):
    attempt_id: str
    run_id: str
    doc_id: str | None
    fingerprint: str | None
    source: SourceStatus


class ContradictingAttempt(_Frozen):
    """A complete listing that does not contain the claim."""

    attempt_id: str
    run_id: str
    doc_id: str | None
    fingerprint: str | None
    same_version: bool  # False: a different or unrecorded code version (differs_across_versions)


class ClaimCounts(_Frozen):
    supporting_ok: int
    supporting_not_ok: int
    distinct_fingerprints: int  # among ok supporting attempts
    contradicting_same_version: int
    contradicting_other_version: int
    confirmed_current: int
    confirmed_other: int
    rejected_current: int
    rejected_other: int


class ClaimAssessment(_Frozen):
    claim_id: str
    name: str
    status: ClaimStatus
    meaning: str
    current_fingerprint: str | None  # None: no --root, currency unknown
    validated_by: tuple[str, ...]  # "rederived", "reviewed", "outcome"
    outcome: OutcomeState = OutcomeState.NONE
    outcomes: tuple[Outcome, ...] = ()  # every outcome for this claim, for the history
    rederivation: Rederivation | None
    supporting: tuple[SupportingAttempt, ...]
    contradicting: tuple[ContradictingAttempt, ...]
    reviews: tuple[Review, ...]
    counts: ClaimCounts
    differs_across_versions: bool


def claim_parts(graph: ExperienceGraph, claim: ExpNode) -> tuple[str | None, str | None]:
    """Subject and object node ids (the dependency-graph ids), or None where absent."""

    def target(kind: EdgeKind) -> str | None:
        edges = graph.out_edges(claim.id, {kind})
        return graph.node(edges[0].target).name if edges else None

    return target(EdgeKind.SUBJECT), target(EdgeKind.OBJECT)


def _contradiction_candidates(
    graph: ExperienceGraph, claim: ExpNode, subject: str | None
) -> list[str]:
    """Attempts that listed what the claim is about completely, possibly without the claim."""
    if claim.predicate == "depends_on" and subject is not None:
        subject_node = f"target:{subject}"
        if not graph.has_node(subject_node):
            # `_clip` shortens very long ids; look the target up by its stored name instead
            matches = [n.id for n in graph.nodes(NodeKind.TARGET) if n.name == subject]
            if not matches:
                return []
            subject_node = matches[0]
        return [e.source for e in graph.in_edges(subject_node, {EdgeKind.ABOUT})]
    if claim.predicate == "is_unresolved" and graph.has_node(UNRESOLVED_CAPABILITY):
        return [e.source for e in graph.in_edges(UNRESOLVED_CAPABILITY, {EdgeKind.USED_CAPABILITY})]
    return []


def outcome_state(outcomes: Iterable[Outcome]) -> OutcomeState:
    """What the outcomes say, from the newest evidence of each source (ADR-0018).

    `outcomes` must be oldest first. A person and a check that disagree (one succeeded, the other
    failed) are a conflict. An ambiguous result never reinforces a claim, and a refused promotion
    (`rejected`) is not evidence.
    """
    latest: dict[OutcomeSource, OutcomeResult] = {}
    for outcome in outcomes:
        if outcome.result in EVIDENCE_RESULTS:
            latest[outcome.source] = outcome.result
    results = set(latest.values())
    if results >= {OutcomeResult.SUCCEEDED, OutcomeResult.FAILED}:
        return OutcomeState.CONFLICT
    if OutcomeResult.FAILED in results:
        return OutcomeState.FAILED
    if OutcomeResult.SUCCEEDED in results:
        return OutcomeState.SUCCEEDED
    if OutcomeResult.AMBIGUOUS in results:
        return OutcomeState.AMBIGUOUS
    return OutcomeState.NONE


def assess_claim(
    graph: ExperienceGraph,
    claim: ExpNode,
    report: SourceReport,
    reviews: list[Review],
    current: CurrentSource | None,
    outcomes: Sequence[Outcome] = (),
) -> ClaimAssessment:
    subject, obj = claim_parts(graph, claim)
    here = current.fingerprint if current else None
    claim_outcomes = [o for o in outcomes if o.claim_id == claim.id]
    current_outcomes = [o for o in claim_outcomes if here is not None and o.fingerprint == here]
    state = outcome_state(current_outcomes)

    evidence_ids = [e.target for e in graph.evidence(claim.id)]
    supporting: list[SupportingAttempt] = []
    for attempt_id in evidence_ids:
        ref = graph.node(attempt_id).ref
        supporting.append(
            SupportingAttempt(
                attempt_id=attempt_id,
                run_id=ref.run_id if ref else "",
                doc_id=ref.doc_id if ref else None,
                fingerprint=ref.source_fingerprint if ref else None,
                source=report.statuses.get(attempt_id, SourceStatus.UNREADABLE),
            )
        )
    ok_support = [s for s in supporting if s.source is SourceStatus.OK]

    rederivation: Rederivation | None = None
    if current is not None and subject is not None and claim.predicate is not None:
        rederivation = current.check(claim.predicate, subject, obj, claim.detail)

    # pro(F): where something backs the claim
    pro: set[str] = {s.fingerprint for s in ok_support if s.fingerprint}
    pro |= {r.fingerprint for r in reviews if r.verdict is Verdict.CONFIRMED}
    if rederivation is not None and rederivation.holding is Holding.HOLDS and here:
        pro.add(here)

    contradicting: list[ContradictingAttempt] = []
    seen = set(evidence_ids)
    for attempt_id in _contradiction_candidates(graph, claim, subject):
        node = graph.node(attempt_id)
        if (
            attempt_id in seen
            or node.coverage != "complete"
            or report.statuses.get(attempt_id) is not SourceStatus.OK
        ):
            continue
        fp = node.ref.source_fingerprint if node.ref else None
        contradicting.append(
            ContradictingAttempt(
                attempt_id=attempt_id,
                run_id=node.ref.run_id if node.ref else "",
                doc_id=node.ref.doc_id if node.ref else None,
                fingerprint=fp,
                same_version=fp is not None and fp in pro,
            )
        )

    # con(F): where something speaks against the claim
    con: set[str] = {c.fingerprint for c in contradicting if c.fingerprint}
    con |= {r.fingerprint for r in reviews if r.verdict is Verdict.REJECTED}
    if rederivation is not None and rederivation.holding is Holding.REFUTED and here:
        con.add(here)

    validated_by: list[str] = []
    if rederivation is not None and rederivation.holding is Holding.HOLDS:
        validated_by.append("rederived")
    if here and any(r.verdict is Verdict.CONFIRMED and r.fingerprint == here for r in reviews):
        validated_by.append("reviewed")
    if state is OutcomeState.SUCCEEDED:
        validated_by.append("outcome")

    if pro & con or state is OutcomeState.CONFLICT:
        status = ClaimStatus.CONFLICTING
    elif state is OutcomeState.FAILED:
        status = ClaimStatus.CONTESTED
    elif validated_by:
        status = ClaimStatus.VALIDATED
    elif state is OutcomeState.AMBIGUOUS:
        status = ClaimStatus.UNKNOWN
    elif not ok_support:
        status = ClaimStatus.UNSUPPORTED
    elif here is not None and here not in {s.fingerprint for s in ok_support}:
        status = ClaimStatus.STALE
    else:
        status = ClaimStatus.SUPPORTED

    def reviewed(verdict: Verdict, *, now: bool) -> int:
        return sum(1 for r in reviews if r.verdict is verdict and (r.fingerprint == here) is now)

    counts = ClaimCounts(
        supporting_ok=len(ok_support),
        supporting_not_ok=len(supporting) - len(ok_support),
        distinct_fingerprints=len({s.fingerprint for s in ok_support if s.fingerprint}),
        contradicting_same_version=sum(1 for c in contradicting if c.same_version),
        contradicting_other_version=sum(1 for c in contradicting if not c.same_version),
        confirmed_current=reviewed(Verdict.CONFIRMED, now=True),
        confirmed_other=reviewed(Verdict.CONFIRMED, now=False),
        rejected_current=reviewed(Verdict.REJECTED, now=True),
        rejected_other=reviewed(Verdict.REJECTED, now=False),
    )
    return ClaimAssessment(
        claim_id=claim.id,
        name=claim.name,
        status=status,
        meaning=STATUS_MEANING[status],
        current_fingerprint=here,
        validated_by=tuple(validated_by) if status is ClaimStatus.VALIDATED else (),
        outcome=state,
        outcomes=tuple(claim_outcomes),
        rederivation=rederivation,
        supporting=tuple(supporting),
        contradicting=tuple(contradicting),
        reviews=tuple(reviews),
        counts=counts,
        differs_across_versions=counts.contradicting_other_version > 0,
    )


def assess_claims(
    graph: ExperienceGraph,
    report: SourceReport,
    reviews: list[Review],
    current: CurrentSource | None,
    outcomes: Sequence[Outcome] = (),
) -> dict[str, ClaimAssessment]:
    """Every claim in the graph, keyed by claim id."""
    by_claim: dict[str, list[Review]] = {}
    for review in reviews:
        by_claim.setdefault(review.claim_id, []).append(review)
    return {
        c.id: assess_claim(graph, c, report, by_claim.get(c.id, []), current, outcomes)
        for c in graph.claims()
    }


def orphaned_reviews(graph: ExperienceGraph, reviews: list[Review]) -> list[Review]:
    """Reviews whose claim is no longer in the graph. Shown, never dropped."""
    return [r for r in reviews if not graph.has_node(r.claim_id)]
