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

Corroboration (ADR-0020) is separate from the status. Independence is by method class per code
version: all analyzer records (trace observations and re-derivations) are one source, all people
(reviews and person outcomes) are one, and `check` outcomes are one. Repeats inside a class are
listed as dependent repeats and add nothing. It is a category, computed on every call.
"""

from collections.abc import Collection, Iterable, Sequence
from datetime import date
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
from cognitive_mesh.validity import (
    Environment,
    EnvScope,
    Validity,
    ValidityState,
    assess_validity,
)

UNRESOLVED_CAPABILITY: Final = "capability:unresolved_report"


class ClaimStatus(StrEnum):
    CONFLICTING = "conflicting"
    CONTESTED = "contested"
    VALIDATED = "validated"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    SUPPORTED = "supported"
    # ADR-0022: shown instead of validated/supported/stale/unsupported, never over a conflict
    EXPIRED = "expired"
    ENVIRONMENT_CHANGED = "environment_changed"
    ENVIRONMENT_UNVERIFIED = "environment_unverified"


class Corroboration(StrEnum):
    NONE = "none"
    SINGLE = "single"
    CORROBORATED = "corroborated"
    CONTRADICTED = "contradicted"


class SourceClass(StrEnum):
    """The only independence rule (ADR-0020): records of one class are one source."""

    ANALYZER = "analyzer"
    PERSON = "person"
    CHECK = "check"


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
    ClaimStatus.EXPIRED: (
        "a review or outcome gave this claim a valid_until date and that date has passed; it is "
        "listed, not dropped, and is neither validated nor corroborated"
    ),
    ClaimStatus.ENVIRONMENT_CHANGED: (
        "the claim is valid only for the environment it was recorded in, and this environment "
        "differs; it is neither validated nor corroborated"
    ),
    ClaimStatus.ENVIRONMENT_UNVERIFIED: (
        "the claim is valid only for the environment it was recorded in, and the two could not "
        "be fully compared; it is neither validated nor corroborated"
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


class SourceGroup(_Frozen):
    """One independent source: a class and every record (all but one are dependent repeats)."""

    source_class: SourceClass
    records: tuple[str, ...]


class VersionCorroboration(_Frozen):
    fingerprint: str | None
    category: Corroboration
    sources: tuple[SourceGroup, ...]
    contradicted_by: tuple[SourceClass, ...]
    reason: str


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
    # ADR-0022: the rule that applied (None: not assessed) and the expiry it used, if any.
    validity: Validity | None = None
    valid_until: date | None = None
    corroboration: Corroboration = Corroboration.NONE
    corroboration_reason: str = ""
    corroboration_version: str | None = None  # the code version the category is about
    sources: tuple[SourceGroup, ...] = ()  # independent sources on that version
    repeats: int = 0  # dependent repeats on that version
    contradicted_by: tuple[SourceClass, ...] = ()
    no_lineage: tuple[str, ...] = ()  # attempts that count for nothing: source not ok, no version
    versions_supported: int = 0  # persistence across code versions, not corroboration
    by_version: tuple[VersionCorroboration, ...] = ()


_RANK: Final = {
    Corroboration.CONTRADICTED: 3,
    Corroboration.CORROBORATED: 2,
    Corroboration.SINGLE: 1,
    Corroboration.NONE: 0,
}


def _sentence(
    fingerprint: str | None,
    category: Corroboration,
    sources: Sequence[SourceGroup],
    against: Sequence[SourceClass],
    other_versions: int,
    no_lineage: int,
) -> str:
    where = f"on code version {fingerprint[:12]}" if fingerprint else "with no code version"
    parts = [
        f"{g.source_class.value} ({len(g.records)} record{'s' if len(g.records) != 1 else ''})"
        for g in sources
    ]
    text = f"{category.value}: "
    if parts:
        text += f"supported {where} by {' and '.join(parts)}, "
        text += f"{len(sources)} independent source{'s' if len(sources) != 1 else ''}"
    else:
        text += f"no supporting record {where}"
    if against:
        text += "; contradicted by " + ", ".join(c.value for c in against)
    if other_versions:
        text += f"; also supported on {other_versions} other code version(s)"
    if no_lineage:
        text += f"; {no_lineage} attempt(s) with no lineage were not counted"
    return text


def corroborate(
    supporting: Sequence[SupportingAttempt],
    contradicting: Sequence[ContradictingAttempt],
    reviews: Sequence[Review],
    outcomes: Sequence[Outcome],
    rederivation: Rederivation | None,
    here: str | None,
) -> tuple[tuple[VersionCorroboration, ...], tuple[str, ...]]:
    """Per code version, which independent source classes support or contradict the claim.

    Returns the versions (always including `here` when given) and the attempts that have no
    lineage (source not confirmed, or no recorded code version) and so count for nothing.
    """
    support: dict[str, dict[SourceClass, list[str]]] = {}
    against: dict[str, set[SourceClass]] = {}

    def pro(fp: str, cls: SourceClass, record: str) -> None:
        support.setdefault(fp, {}).setdefault(cls, []).append(record)

    def con(fp: str, cls: SourceClass) -> None:
        against.setdefault(fp, set()).add(cls)

    no_lineage: list[str] = []
    for s in supporting:
        if s.source is SourceStatus.OK and s.fingerprint:
            pro(s.fingerprint, SourceClass.ANALYZER, f"run:{s.run_id}")
        else:
            no_lineage.append(s.attempt_id)
    if rederivation is not None and here:
        if rederivation.holding is Holding.HOLDS:
            pro(here, SourceClass.ANALYZER, "rederivation")
        elif rederivation.holding is Holding.REFUTED:
            con(here, SourceClass.ANALYZER)
    for fp in {c.fingerprint for c in contradicting if c.fingerprint}:
        con(fp, SourceClass.ANALYZER)
    for r in reviews:
        if r.verdict is Verdict.CONFIRMED:
            pro(r.fingerprint, SourceClass.PERSON, f"review:#{r.id}")
        elif r.verdict is Verdict.REJECTED:
            con(r.fingerprint, SourceClass.PERSON)
    for o in outcomes:
        if o.source is OutcomeSource.PROMOTION or not o.fingerprint:
            continue
        cls = SourceClass.PERSON if o.source is OutcomeSource.PERSON else SourceClass.CHECK
        if o.result is OutcomeResult.SUCCEEDED:
            pro(o.fingerprint, cls, f"outcome:#{o.id}")
        elif o.result is OutcomeResult.FAILED:
            con(o.fingerprint, cls)

    fingerprints = sorted(set(support) | ({here} if here else set()))
    built: list[VersionCorroboration] = []
    for fp in fingerprints:
        groups = tuple(
            SourceGroup(source_class=cls, records=tuple(records))
            for cls, records in sorted(support.get(fp, {}).items())
        )
        blocked = tuple(sorted(against.get(fp, set())))
        if blocked:
            category = Corroboration.CONTRADICTED
        elif len(groups) >= 2:
            category = Corroboration.CORROBORATED
        elif groups:
            category = Corroboration.SINGLE
        else:
            category = Corroboration.NONE
        others = len([f for f in support if f != fp])
        built.append(
            VersionCorroboration(
                fingerprint=fp,
                category=category,
                sources=groups,
                contradicted_by=blocked,
                reason=_sentence(fp, category, groups, blocked, others, len(no_lineage)),
            )
        )
    if not built:
        built.append(
            VersionCorroboration(
                fingerprint=None,
                category=Corroboration.NONE,
                sources=(),
                contradicted_by=(),
                reason=_sentence(None, Corroboration.NONE, (), (), 0, len(no_lineage)),
            )
        )
    return tuple(built), tuple(no_lineage)


def _reported(versions: Sequence[VersionCorroboration], here: str | None) -> VersionCorroboration:
    """The current version when known, else the strongest one (contradiction ranks first)."""
    if here:
        return next(v for v in versions if v.fingerprint == here)
    return max(versions, key=lambda v: (_RANK[v.category], len(v.sources)))


def claim_parts(graph: ExperienceGraph, claim: ExpNode) -> tuple[str | None, str | None]:
    """Subject and object node ids (the dependency-graph ids), or None where absent."""

    def target(kind: EdgeKind) -> str | None:
        edges = graph.out_edges(claim.id, {kind})
        return graph.node(edges[0].target).name if edges else None

    return target(EdgeKind.SUBJECT), target(EdgeKind.OBJECT)


def _same_application(graph: ExperienceGraph, claim: ExpNode, attempts: list[str]) -> list[str]:
    """A listing of another or an unrecorded application never contradicts the claim (ADR-0021)."""
    return [a for a in attempts if graph.node(a).app_id == claim.app_id]


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
        return _same_application(
            graph, claim, [e.source for e in graph.in_edges(subject_node, {EdgeKind.ABOUT})]
        )
    if claim.predicate == "is_unresolved" and graph.has_node(UNRESOLVED_CAPABILITY):
        return _same_application(
            graph,
            claim,
            [e.source for e in graph.in_edges(UNRESOLVED_CAPABILITY, {EdgeKind.USED_CAPABILITY})],
        )
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


_VALIDITY_STATUS: Final = {
    ValidityState.EXPIRED: ClaimStatus.EXPIRED,
    ValidityState.ENVIRONMENT_CHANGED: ClaimStatus.ENVIRONMENT_CHANGED,
    ValidityState.ENVIRONMENT_UNVERIFIED: ClaimStatus.ENVIRONMENT_UNVERIFIED,
}


def effective_valid_until(
    reviews: Iterable[Review], outcomes: Iterable[Outcome], here: str | None
) -> date | None:
    """The expiry of the newest review or outcome that sets one (ADR-0022).

    Only records for the current code version count when it is known. A newer record that sets a
    later date renews the claim; a newer record without a date does not cancel an older expiry.
    """
    records: list[Review | Outcome] = [*reviews, *outcomes]
    dated = [
        (r.created_at, r.valid_until)
        for r in records
        if r.valid_until is not None and (here is None or r.fingerprint == here)
    ]
    return max(dated, key=lambda d: d[0])[1] if dated else None


class _Listings:
    """What one `assess_claims` call computes once for many claims (ADR-0028).

    Claims about one subject (and one application) are contradicted by the same listings, and
    `ContradictingAttempt` is frozen, so the listings and their objects are built once and shared.
    Valid for one graph and one source report only; nothing outlives the call.
    """

    def __init__(self, graph: ExperienceGraph, report: SourceReport) -> None:
        self._graph = graph
        self._report = report
        self._usable: dict[tuple[str | None, str | None, str | None], tuple[str, ...]] = {}
        self._objects: dict[tuple[str, bool], ContradictingAttempt] = {}

    def usable(self, claim: ExpNode, subject: str | None) -> tuple[str, ...]:
        """Candidate attempts that are complete listings whose source is ok, in graph order."""
        key = (claim.predicate, subject if claim.predicate == "depends_on" else None, claim.app_id)
        found = self._usable.get(key)
        if found is None:
            graph, statuses = self._graph, self._report.statuses
            found = tuple(
                a
                for a in _contradiction_candidates(graph, claim, subject)
                if graph.node(a).coverage == "complete" and statuses.get(a) is SourceStatus.OK
            )
            self._usable[key] = found
        return found

    def attempt(self, attempt_id: str, *, same_version_of: set[str]) -> ContradictingAttempt:
        node = self._graph.node(attempt_id)
        fp = node.ref.source_fingerprint if node.ref else None
        same = fp is not None and fp in same_version_of
        key = (attempt_id, same)
        made = self._objects.get(key)
        if made is None:
            made = ContradictingAttempt(
                attempt_id=attempt_id,
                run_id=node.ref.run_id if node.ref else "",
                doc_id=node.ref.doc_id if node.ref else None,
                fingerprint=fp,
                same_version=same,
            )
            self._objects[key] = made
        return made


def assess_claim(
    graph: ExperienceGraph,
    claim: ExpNode,
    report: SourceReport,
    reviews: list[Review],
    current: CurrentSource | None,
    outcomes: Sequence[Outcome] = (),
    *,
    today: date | None = None,
    environment: Environment | None = None,
    listings: _Listings | None = None,
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

    if listings is None:
        listings = _Listings(graph, report)
    seen = set(evidence_ids)
    contradicting = [
        listings.attempt(a, same_version_of=pro)
        for a in listings.usable(claim, subject)
        if a not in seen
    ]

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

    until = effective_valid_until(reviews, claim_outcomes, here)
    validity = assess_validity(
        claim.scope or EnvScope.UNSCOPED,
        claim.environment or Environment(),
        environment or Environment(),
        until,
        today,
    )
    if status not in (ClaimStatus.CONFLICTING, ClaimStatus.CONTESTED):
        status = _VALIDITY_STATUS.get(validity.state, status)

    def reviewed(verdict: Verdict, *, now: bool) -> int:
        return sum(1 for r in reviews if r.verdict is verdict and (r.fingerprint == here) is now)

    versions, no_lineage = corroborate(
        supporting, contradicting, reviews, claim_outcomes, rederivation, here
    )
    shown = _reported(versions, here)
    category, reason = shown.category, shown.reason
    if status in _VALIDITY_STATUS.values() and category is Corroboration.CORROBORATED:
        category = Corroboration.SINGLE
        reason = f"{reason} (not corroborated while the claim is {status.value})"

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
        validity=validity,
        valid_until=until,
        corroboration=category,
        corroboration_reason=reason,
        corroboration_version=shown.fingerprint,
        sources=shown.sources,
        repeats=sum(len(g.records) - 1 for g in shown.sources),
        contradicted_by=shown.contradicted_by,
        no_lineage=no_lineage,
        versions_supported=sum(1 for v in versions if v.sources),
        by_version=versions,
    )


def assess_claims(
    graph: ExperienceGraph,
    report: SourceReport,
    reviews: list[Review],
    current: CurrentSource | None,
    outcomes: Sequence[Outcome] = (),
    only: Collection[str] | None = None,
    *,
    today: date | None = None,
    environment: Environment | None = None,
) -> dict[str, ClaimAssessment]:
    """Every claim in the graph, keyed by claim id; with `only`, just those claim ids."""
    by_claim: dict[str, list[Review]] = {}
    for review in reviews:
        by_claim.setdefault(review.claim_id, []).append(review)
    outcomes_by_claim: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        outcomes_by_claim.setdefault(outcome.claim_id, []).append(outcome)
    listings = _Listings(graph, report)
    return {
        c.id: assess_claim(
            graph,
            c,
            report,
            by_claim.get(c.id, []),
            current,
            outcomes_by_claim.get(c.id, []),
            today=today,
            environment=environment,
            listings=listings,
        )
        for c in graph.claims()
        if only is None or c.id in only
    }


def claims_of_subjects(graph: ExperienceGraph, subjects: Collection[str]) -> list[ExpNode]:
    """The claims about these subjects (ADR-0019), read from each claim's stored subject."""
    return [c for c in graph.claims() if c.subject in subjects]


def attempts_behind(graph: ExperienceGraph, claims: Iterable[ExpNode]) -> set[str]:
    """Attempts `assess_claim` reads for these claims: evidence and contradiction candidates."""
    found: set[str] = set()
    for claim in claims:
        found.update(e.target for e in graph.evidence(claim.id))
        subject, _ = claim_parts(graph, claim)
        found.update(_contradiction_candidates(graph, claim, subject))
    return found


def orphaned_reviews(graph: ExperienceGraph, reviews: list[Review]) -> list[Review]:
    """Reviews whose claim is no longer in the graph. Shown, never dropped."""
    return [r for r in reviews if not graph.has_node(r.claim_id)]
