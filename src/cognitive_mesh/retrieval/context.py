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
    MAX_RAW,
    MAX_REFS_PER_CLAIM,
    NO_PRIOR_CONTEXT,
    ClaimItem,
    ContradictingRun,
    RawItem,
    RecalledContext,
    SourceRun,
    Withheld,
)
from cognitive_mesh.experience.assess import claim_parts
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.graph import DependencyGraph, GraphError
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
) -> GatedContext:
    """The gated pack for one task. `current_root` is the directory the task will run against."""
    here = root_name(current_root)
    unconfirmed = len(result.unconfirmed)
    held_back: list[NotApplicable] = []
    claims: list[ClaimItem] = []
    for recalled in result.claims:
        subject, obj = claim_parts(experience, experience.node(recalled.assessment.claim_id))
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
        claims.append(_claim_item(recalled, experience, subject, obj))

    raw: list[RawItem] = []
    for item in result.experiences_without_claims:
        try:
            source_root = store.get(item.run_id).source_root
        except (TraceNotFoundError, TraceStoreError):
            unconfirmed += 1
            continue
        if source_root is None or root_name(source_root) != here:
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
            )
        )

    over_limit = max(0, len(claims) - MAX_CLAIMS) + max(0, len(raw) - MAX_RAW)
    claims, raw = claims[:MAX_CLAIMS], raw[:MAX_RAW]
    empty = not claims and not raw
    pack = RecalledContext(
        message=NO_PRIOR_CONTEXT if empty else "",
        query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        current_fingerprint=result.current_fingerprint,
        k=result.k,
        min_similarity=result.min_similarity,
        claims=tuple(claims),
        raw=tuple(raw),
        withheld=Withheld(
            unconfirmed=unconfirmed, not_applicable=len(held_back), over_limit=over_limit
        ),
        empty=empty,
    )
    return GatedContext(pack=pack, not_applicable=tuple(held_back))
