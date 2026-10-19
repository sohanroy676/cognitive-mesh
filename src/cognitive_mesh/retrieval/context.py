"""Turn a recall result into the pack an agent may read (ADR-0017).

Two gates keep unrelated experience away from a task. A claim is offered only if its subject exists
in the application's current dependency graph; a raw experience only if its run analysed a
directory with the same name. What fails a gate is counted in `withheld`, never sent. Unconfirmed
hits are counted too. No excerpt of any trace and no review note is copied into the pack.
"""

import hashlib
from pathlib import PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.agents.context import (
    MAX_CLAIMS,
    MAX_DROPPED_LISTED,
    MAX_RAW,
    MAX_REFS_PER_CLAIM,
    NO_PRIOR_CONTEXT,
    ClaimItem,
    ContextProfile,
    ContradictingRun,
    DroppedItem,
    RawItem,
    RecalledContext,
    SourceRun,
    Withheld,
)
from cognitive_mesh.experience.assess import ClaimStatus, claim_parts
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.outcomes import EVIDENCE_RESULTS
from cognitive_mesh.graph import DependencyGraph, GraphError
from cognitive_mesh.identity import AppIdentity
from cognitive_mesh.retrieval.models import RecalledClaim, RecallResult
from cognitive_mesh.traces import TraceNotFoundError, TraceStore, TraceStoreError


class NotApplicable(BaseModel):
    """A hit that failed the relevance gate. Shown to the user; never sent to an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["claim", "raw"]
    label: str  # a claim's text or a run/step id: untrusted data
    reason: str
    similarity: float


class GatedContext(BaseModel):
    """The pack for the agents, and what was kept out of it, for the person."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack: RecalledContext
    not_applicable: tuple[NotApplicable, ...]


REASON_SUBJECT = "the claim's subject is not in the current application"
REASON_OTHER_DIRECTORY = "the run analysed a directory with a different name"
REASON_NO_DIRECTORY = "the run recorded no directory"
REASON_OTHER_APPLICATION = "the claim comes from a different application id"
REASON_EXPIRED = "the claim's valid_until date has passed"
REASON_ENVIRONMENT_CHANGED = "the claim is valid for a different environment"
_WITHHELD_BY_VALIDITY = {
    ClaimStatus.EXPIRED.value: REASON_EXPIRED,
    ClaimStatus.ENVIRONMENT_CHANGED.value: REASON_ENVIRONMENT_CHANGED,
}
REASON_OTHER_APPLICATION_RAW = "the run analysed a different application id"


# ADR-0023: the written selection order. Lower sorts first; conflicts are never cut for size.
CONFLICT_STATUSES = frozenset({ClaimStatus.CONFLICTING.value, ClaimStatus.CONTESTED.value})
_STATUS_GROUP = {
    **{s: 0 for s in CONFLICT_STATUSES},
    **{
        s.value: 1
        for s in (
            ClaimStatus.STALE,
            ClaimStatus.EXPIRED,
            ClaimStatus.ENVIRONMENT_CHANGED,
            ClaimStatus.ENVIRONMENT_UNVERIFIED,
            ClaimStatus.UNKNOWN,
        )
    },
    ClaimStatus.VALIDATED.value: 2,
    ClaimStatus.SUPPORTED.value: 3,
    ClaimStatus.UNSUPPORTED.value: 4,
}
_CORROBORATION_RANK = {"corroborated": 0, "contradicted": 1, "single": 2, "none": 3}


def claim_order_key(item: ClaimItem) -> tuple[int, int, float, str]:
    """Status group, then corroboration, then best similarity (descending), then claim id."""
    return (
        _STATUS_GROUP.get(item.status, len(_STATUS_GROUP)),
        _CORROBORATION_RANK.get(item.corroboration, len(_CORROBORATION_RANK)),
        -item.best_similarity,
        item.claim_id,
    )


def select_claims(
    claims: list[ClaimItem],
) -> tuple[list[ClaimItem], list[ClaimItem], int, bool]:
    """(kept, dropped, conflicts kept, conflicts alone over the limit). Pure and deterministic."""
    ordered = sorted(claims, key=claim_order_key)
    conflicts = [c for c in ordered if c.status in CONFLICT_STATUSES]
    others = [c for c in ordered if c.status not in CONFLICT_STATUSES]
    room = max(0, MAX_CLAIMS - len(conflicts))
    return conflicts + others[:room], others[room:], len(conflicts), len(conflicts) > MAX_CLAIMS


def root_name(path: str) -> str:
    """The last component of a directory path, for either kind of separator."""
    return PureWindowsPath(path).name


def _in_graph(graph: DependencyGraph, node_id: str | None) -> bool:
    if node_id is None:
        return False
    try:
        graph.node(node_id)
    except GraphError:
        return False
    return True


def _claim_item(
    recalled: RecalledClaim, experience: ExperienceGraph, subject: str | None, obj: str | None
) -> ClaimItem:
    a = recalled.assessment
    node = experience.node(a.claim_id)
    ok_support = [s for s in a.supporting if s.source.value == "ok"]
    evidence = [o for o in a.outcomes if o.result in EVIDENCE_RESULTS]
    outcomes_current = sum(1 for o in evidence if o.fingerprint == a.current_fingerprint)
    return ClaimItem(
        claim_id=a.claim_id,
        name=a.name,
        predicate=node.predicate,
        subject=subject,
        object=obj,
        detail=node.detail,
        status=a.status.value,
        meaning=a.meaning,
        validated_by=a.validated_by,
        corroboration=a.corroboration.value,
        corroboration_reason=a.corroboration_reason,
        supporting=tuple(SourceRun(run_id=s.run_id, doc_id=s.doc_id) for s in ok_support)[
            :MAX_REFS_PER_CLAIM
        ],
        supporting_total=len(ok_support),
        contradicting=tuple(
            ContradictingRun(run_id=c.run_id, doc_id=c.doc_id, same_version=c.same_version)
            for c in a.contradicting
        )[:MAX_REFS_PER_CLAIM],
        contradicting_total=len(a.contradicting),
        confirmed_reviews=a.counts.confirmed_current + a.counts.confirmed_other,
        rejected_reviews=a.counts.rejected_current + a.counts.rejected_other,
        outcome=a.outcome.value,
        outcomes_current=outcomes_current,
        outcomes_other=len(evidence) - outcomes_current,
        retrieved_via_doc_ids=tuple(v.doc_id for v in recalled.retrieved_via),
        best_similarity=max((v.similarity for v in recalled.retrieved_via), default=0.0),
    )


def build_context(
    query: str,
    result: RecallResult,
    experience: ExperienceGraph,
    current_graph: DependencyGraph,
    store: TraceStore,
    current_root: str,
    identity: AppIdentity | None = None,
    profile: ContextProfile = "precision",
    outcomes_consulted: bool | None = None,
) -> GatedContext:
    """The gated pack for one task. `current_root` is the directory the task will run against.

    With an application id (ADR-0021), items recorded under another id are held back; items recorded
    without one keep the older gate and are labelled `unrecorded`. The id is a label, not proof.
    The `recall` profile (ADR-0023) also offers raw traces of id-less runs from a directory with
    another name, labelled `unrecorded`; every other gate is the same in both profiles.
    """
    here = root_name(current_root)
    unconfirmed = len(result.unconfirmed)
    held_back: list[NotApplicable] = []
    claims: list[ClaimItem] = []
    for recalled in result.claims:
        node = experience.node(recalled.assessment.claim_id)
        subject, obj = claim_parts(experience, node)
        if identity is not None and node.app_id is not None and node.app_id != identity.app_id:
            held_back.append(
                NotApplicable(
                    kind="claim",
                    label=recalled.assessment.name,
                    reason=REASON_OTHER_APPLICATION,
                    similarity=max((v.similarity for v in recalled.retrieved_via), default=0.0),
                )
            )
            continue
        withheld_for = _WITHHELD_BY_VALIDITY.get(recalled.assessment.status.value)
        if withheld_for is not None:
            held_back.append(
                NotApplicable(
                    kind="claim",
                    label=recalled.assessment.name,
                    reason=withheld_for,
                    similarity=max((v.similarity for v in recalled.retrieved_via), default=0.0),
                )
            )
            continue
        if not _in_graph(current_graph, subject):
            held_back.append(
                NotApplicable(
                    kind="claim",
                    label=recalled.assessment.name,
                    reason=REASON_SUBJECT,
                    similarity=max((v.similarity for v in recalled.retrieved_via), default=0.0),
                )
            )
            continue
        claim_item = _claim_item(recalled, experience, subject, obj)
        claims.append(
            claim_item.model_copy(
                update={
                    "identity": "matched"
                    if node.app_id is not None and identity is not None
                    else "unrecorded"
                }
            )
        )

    raw: list[RawItem] = []
    for item in result.experiences_without_claims:
        try:
            trace = store.get(item.run_id)
        except (TraceNotFoundError, TraceStoreError):
            unconfirmed += 1
            continue
        source_root = trace.source_root
        matched: Literal["matched", "unrecorded"] = "unrecorded"
        if identity is not None and trace.app_id is not None:
            if trace.app_id != identity.app_id:
                held_back.append(
                    NotApplicable(
                        kind="raw",
                        label=f"{item.run_id}/{item.step_id}/{item.attempt_no}",
                        reason=REASON_OTHER_APPLICATION_RAW,
                        similarity=item.similarity,
                    )
                )
                continue
            matched = "matched"
        elif (source_root is None or root_name(source_root) != here) and (
            profile == "precision" or trace.app_id is not None
        ):
            held_back.append(
                NotApplicable(
                    kind="raw",
                    label=f"{item.run_id}/{item.step_id}/{item.attempt_no}",
                    reason=REASON_NO_DIRECTORY if source_root is None else REASON_OTHER_DIRECTORY,
                    similarity=item.similarity,
                )
            )
            continue
        raw.append(
            RawItem(
                run_id=item.run_id,
                step_id=item.step_id,
                attempt_no=item.attempt_no,
                capability=item.capability,
                kind=item.kind,
                result_status=item.result_status,
                similarity=item.similarity,
                identity=matched,
            )
        )

    claims, dropped_claims, conflicts_kept, conflicts_over = select_claims(claims)
    raw.sort(key=lambda r: (-r.similarity, r.run_id, r.step_id, r.attempt_no))
    raw, dropped_raw = raw[:MAX_RAW], raw[MAX_RAW:]
    dropped = tuple(
        [DroppedItem(kind="claim", item_id=c.claim_id, status=c.status) for c in dropped_claims]
        + [
            DroppedItem(kind="raw", item_id=f"{r.run_id}/{r.step_id}/{r.attempt_no}")
            for r in dropped_raw
        ]
    )
    empty = not claims and not raw
    pack = RecalledContext(
        message=NO_PRIOR_CONTEXT if empty else "",
        query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        current_fingerprint=result.current_fingerprint,
        k=result.k,
        min_similarity=result.min_similarity,
        profile=profile,
        conflicts_kept=conflicts_kept,
        conflicts_over_limit=conflicts_over,
        outcomes_consulted=outcomes_consulted,
        claims=tuple(claims),
        raw=tuple(raw),
        withheld=Withheld(
            unconfirmed=unconfirmed,
            not_applicable=len(held_back),
            over_limit=len(dropped),
            dropped=dropped[:MAX_DROPPED_LISTED],
        ),
        empty=empty,
    )
    return GatedContext(pack=pack, not_applicable=tuple(held_back))
