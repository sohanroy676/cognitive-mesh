"""Decision status (ADR-0039): pure rules, no I/O, no model, no clock.

A decision's status says whether the evidence it cites still holds for the code as it is now and
whether what it led to worked out. It is computed on every call and never stored. The reasoning
text of a decision is never evidence for itself, a model-written decision is never validated by
being repeated, and the success of the run a decision was made in counts for nothing: only a
recorded outcome for the current code version can validate.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.decisions.models import CitationKind, Decision
from cognitive_mesh.experience.assess import ClaimStatus, OutcomeState, outcome_state
from cognitive_mesh.experience.models import SourceStatus
from cognitive_mesh.experience.outcomes import Outcome


class DecisionStatus(StrEnum):
    CONFLICTING = "conflicting"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    VALIDATED = "validated"
    UNKNOWN = "unknown"
    SUPPORTED = "supported"


STATUS_MEANING: Final[dict[DecisionStatus, str]] = {
    DecisionStatus.CONFLICTING: (
        "recorded outcomes disagree, or another current decision replaces the same earlier "
        "decision; both sides are listed"
    ),
    DecisionStatus.CONTESTED: "a recorded outcome for the current code version says it failed",
    DecisionStatus.SUPERSEDED: "a later decision replaces it; it stays as history",
    DecisionStatus.UNSUPPORTED: "it cites nothing, or something it cites is gone",
    DecisionStatus.STALE: "something it cites changed or is itself no longer sound",
    DecisionStatus.VALIDATED: (
        "a recorded outcome for the current code version says it worked (not proof)"
    ),
    DecisionStatus.UNKNOWN: "the recorded outcome for the current code version is ambiguous",
    DecisionStatus.SUPPORTED: (
        "what it cites still holds; nothing checks the reasoning, and nothing shows it worked"
    ),
}


class CitationState(StrEnum):
    OK = "ok"  # the cited thing is there and unchanged
    CHANGED = "changed"  # a cited file now has other content
    WEAK = "weak"  # it exists, but is itself stale, conflicting, contested, expired, ...
    ABSENT = "absent"  # gone, or its own source is gone
    UNKNOWN = "unknown"  # no graph or no --root, so it cannot be checked


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CitationCheck(_Frozen):
    kind: CitationKind
    target: str
    state: CitationState
    reason: str  # fixed wording
    cited_status: str | None = None  # the status of a cited claim or decision


class DecisionAssessment(_Frozen):
    decision_id: str
    summary: str
    status: DecisionStatus
    meaning: str
    basis: str  # "person" | "model_unverified"
    current_fingerprint: str | None  # None: no --root, currency unknown
    outcome: OutcomeState
    outcomes: tuple[Outcome, ...]
    citations: tuple[CitationCheck, ...]
    superseded_by: tuple[str, ...]
    competing: tuple[str, ...]  # other current decisions that replace the same earlier one
    reasons: tuple[str, ...]


_WEAK_CLAIM: Final = frozenset(
    {
        ClaimStatus.STALE,
        ClaimStatus.CONFLICTING,
        ClaimStatus.CONTESTED,
        ClaimStatus.EXPIRED,
        ClaimStatus.ENVIRONMENT_CHANGED,
    }
)
_GONE_CLAIM: Final = frozenset({ClaimStatus.UNSUPPORTED})
_SOUND_DECISION: Final = frozenset(
    {DecisionStatus.VALIDATED, DecisionStatus.SUPPORTED, DecisionStatus.UNKNOWN}
)


def _node_check(
    target: str,
    node_ids: Collection[str] | None,
    statuses: Mapping[str, SourceStatus] | None,
    claim_status: Mapping[str, ClaimStatus],
) -> CitationCheck:
    if node_ids is None:
        return CitationCheck(
            kind=CitationKind.NODE,
            target=target,
            state=CitationState.UNKNOWN,
            reason="no graph was given, so the cited node cannot be checked",
        )
    if target not in node_ids:
        return CitationCheck(
            kind=CitationKind.NODE,
            target=target,
            state=CitationState.ABSENT,
            reason="the cited node is not in the graph (rebuild it if it is new)",
        )
    source = (statuses or {}).get(target)
    if source is not None and source is not SourceStatus.OK:
        return CitationCheck(
            kind=CitationKind.NODE,
            target=target,
            state=CitationState.ABSENT,
            reason=f"the cited node rests on a source that is {source.value}",
        )
    claim = claim_status.get(target)
    if claim is not None and claim in _GONE_CLAIM:
        return CitationCheck(
            kind=CitationKind.NODE,
            target=target,
            state=CitationState.ABSENT,
            reason="the cited claim has no supporting record any more",
            cited_status=claim.value,
        )
    if claim is not None and claim in _WEAK_CLAIM:
        return CitationCheck(
            kind=CitationKind.NODE,
            target=target,
            state=CitationState.WEAK,
            reason=f"the cited claim is {claim.value}",
            cited_status=claim.value,
        )
    return CitationCheck(
        kind=CitationKind.NODE,
        target=target,
        state=CitationState.OK,
        reason="the cited node is in the graph",
        cited_status=claim.value if claim is not None else None,
    )


def _file_check(
    target: str, recorded: str | None, hashes: Mapping[str, str] | None
) -> CitationCheck:
    if hashes is None:
        return CitationCheck(
            kind=CitationKind.FILE,
            target=target,
            state=CitationState.UNKNOWN,
            reason="no --root was given, so the cited file cannot be checked",
        )
    now = hashes.get(target)
    if now is None:
        return CitationCheck(
            kind=CitationKind.FILE,
            target=target,
            state=CitationState.ABSENT,
            reason="the cited file is not in the application any more",
        )
    if now != recorded:
        return CitationCheck(
            kind=CitationKind.FILE,
            target=target,
            state=CitationState.CHANGED,
            reason="the cited file has other content than when the decision was recorded",
        )
    return CitationCheck(
        kind=CitationKind.FILE,
        target=target,
        state=CitationState.OK,
        reason="the cited file is unchanged",
    )


def assess_decisions(
    decisions: Sequence[Decision],
    *,
    node_ids: Collection[str] | None = None,
    source_statuses: Mapping[str, SourceStatus] | None = None,
    claim_status: Mapping[str, ClaimStatus] | None = None,
    file_hashes: Mapping[str, str] | None = None,
    fingerprint: str | None = None,
    outcomes: Iterable[Outcome] = (),
    today: date | None = None,
) -> dict[str, DecisionAssessment]:
    """Assess every given decision. `node_ids` None means no graph; `file_hashes` None, no --root.

    `fingerprint` is the code version now (None: unknown, so no outcome counts). Outcomes are
    taken oldest first, only for that version, and an outcome past its `valid_until` is ignored.
    """
    claims = claim_status or {}
    by_id = {d.decision_id: d for d in decisions}
    successors: dict[str, list[str]] = {}
    for d in decisions:
        if d.supersedes is not None:
            successors.setdefault(d.supersedes, []).append(d.decision_id)
    grouped: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.claim_id in by_id and fingerprint is not None and o.fingerprint == fingerprint:
            if today is not None and o.valid_until is not None and o.valid_until < today:
                continue
            grouped.setdefault(o.claim_id, []).append(o)
    all_outcomes: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.claim_id in by_id:
            all_outcomes.setdefault(o.claim_id, []).append(o)

    done: dict[str, DecisionAssessment] = {}
    active: set[str] = set()

    def assess(decision_id: str) -> DecisionAssessment:
        if decision_id in done:
            return done[decision_id]
        decision = by_id[decision_id]
        active.add(decision_id)
        checks: list[CitationCheck] = []
        for cite in decision.citations:
            if cite.kind is CitationKind.FILE:
                checks.append(_file_check(cite.target, cite.sha256, file_hashes))
            elif cite.kind is CitationKind.NODE:
                checks.append(_node_check(cite.target, node_ids, source_statuses, claims))
            else:
                checks.append(_decision_check(cite.target))
        state = outcome_state(grouped.get(decision_id, []))
        later = tuple(sorted(successors.get(decision_id, [])))
        heads = [s for s in successors.get(decision.supersedes or "", []) if s not in successors]
        competing = (
            tuple(sorted(s for s in heads if s != decision_id))
            if decision.supersedes is not None and decision_id not in successors
            else ()
        )
        status, reasons = _decide(decision, checks, state, later, competing, fingerprint)
        active.discard(decision_id)
        result = DecisionAssessment(
            decision_id=decision_id,
            summary=decision.summary,
            status=status,
            meaning=STATUS_MEANING[status],
            basis=decision.basis.value,
            current_fingerprint=fingerprint,
            outcome=state,
            outcomes=tuple(all_outcomes.get(decision_id, [])),
            citations=tuple(checks),
            superseded_by=later,
            competing=competing,
            reasons=tuple(reasons),
        )
        done[decision_id] = result
        return result

    def _decision_check(target: str) -> CitationCheck:
        if target not in by_id:
            return CitationCheck(
                kind=CitationKind.DECISION,
                target=target,
                state=CitationState.ABSENT,
                reason="the cited decision is not readable",
            )
        if target in active:  # cannot happen for ids that are hashes; never loop
            return CitationCheck(
                kind=CitationKind.DECISION,
                target=target,
                state=CitationState.UNKNOWN,
                reason="the citations form a cycle",
            )
        status = assess(target).status
        sound = status in _SOUND_DECISION or status is DecisionStatus.SUPERSEDED
        return CitationCheck(
            kind=CitationKind.DECISION,
            target=target,
            state=CitationState.OK if sound else CitationState.WEAK,
            reason=f"the cited decision is {status.value}",
            cited_status=status.value,
        )

    for decision_id in sorted(by_id):
        assess(decision_id)
    return done


def _decide(
    decision: Decision,
    checks: Sequence[CitationCheck],
    state: OutcomeState,
    later: Sequence[str],
    competing: Sequence[str],
    fingerprint: str | None,
) -> tuple[DecisionStatus, list[str]]:
    reasons = [
        f"{c.kind.value} {c.target}: {c.reason}" for c in checks if c.state is not CitationState.OK
    ]
    unknown = sum(1 for c in checks if c.state is CitationState.UNKNOWN)
    if fingerprint is None:
        reasons.append("no --root was given, so no recorded outcome can be matched to the code")
    if state is OutcomeState.CONFLICT:
        return DecisionStatus.CONFLICTING, ["recorded outcomes disagree", *reasons]
    if competing:
        return DecisionStatus.CONFLICTING, [
            f"{len(competing)} other current decision(s) replace the same earlier decision",
            *reasons,
        ]
    if state is OutcomeState.FAILED:
        return DecisionStatus.CONTESTED, ["a recorded outcome says it failed", *reasons]
    if later:
        return DecisionStatus.SUPERSEDED, [f"replaced by {', '.join(later)}"]
    if not checks:
        return DecisionStatus.UNSUPPORTED, ["it cites no evidence"]
    if any(c.state is CitationState.ABSENT for c in checks):
        return DecisionStatus.UNSUPPORTED, reasons
    if any(c.state in (CitationState.CHANGED, CitationState.WEAK) for c in checks):
        return DecisionStatus.STALE, reasons
    if state is OutcomeState.SUCCEEDED:
        return DecisionStatus.VALIDATED, ["a recorded outcome says it worked", *reasons]
    if state is OutcomeState.AMBIGUOUS:
        return DecisionStatus.UNKNOWN, ["the recorded outcome is ambiguous", *reasons]
    if unknown:
        reasons.append(f"{unknown} citation(s) could not be checked")
    if decision.basis.value == "model_unverified":
        reasons.append("the reasoning was written by a model and is not verified")
    return DecisionStatus.SUPPORTED, reasons
