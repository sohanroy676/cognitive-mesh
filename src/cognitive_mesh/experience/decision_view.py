"""Assemble what the decision status rules need, and the JSON for `decisions` (ADR-0039).

The rules themselves are in `decision_assess.py` and do no I/O. This module reads the stores the
person gave (trace database, optional graph, reviews, outcomes, application root) and hands the
rules plain values. Every input is optional: without it the matching citations read `unknown`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cognitive_mesh.decisions.models import (
    CitationKind,
    Decision,
    DecisionError,
    DecisionLink,
    DecisionListing,
    LinkKind,
)
from cognitive_mesh.experience.assess import ClaimStatus, assess_claims, attempts_behind
from cognitive_mesh.experience.current import CurrentSource
from cognitive_mesh.experience.decision_assess import DecisionAssessment, assess_decisions
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import NodeKind, SourceStatus
from cognitive_mesh.experience.outcomes import Outcome
from cognitive_mesh.experience.reviews import Review
from cognitive_mesh.experience.verify import SourceReport, check_attempts, check_nodes
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.traces import TraceStore, source_fingerprint


@dataclass(frozen=True)
class DecisionsView:
    listing: DecisionListing
    assessments: dict[str, DecisionAssessment]
    graph_given: bool
    root_given: bool


def assess_all(
    store: TraceStore,
    *,
    graph: ExperienceGraph | None = None,
    reviews: list[Review] | None = None,
    outcomes: list[Outcome] | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> DecisionsView:
    """Read every decision and assess it. Raises `DecisionError` for an unusable root."""
    listing = store.read_decisions()
    hashes: dict[str, str] | None = None
    current: CurrentSource | None = None
    if root is not None:
        try:
            ingestion = ingest_application(root)
        except (IngestionError, OSError):
            raise DecisionError("the application root cannot be read") from None
        current = CurrentSource(source_fingerprint(ingestion), build_dependency_graph(ingestion))
        hashes = {f.ref.path: f.ref.sha256 for f in ingestion.files}

    node_ids: set[str] | None = None
    statuses: dict[str, SourceStatus] = {}
    claim_status: dict[str, ClaimStatus] = {}
    if graph is not None:
        node_ids = {n.id for n in graph.nodes()}
        cited = {
            c.target
            for d in listing.decisions
            for c in d.citations
            if c.kind is CitationKind.NODE and c.target in node_ids
        }
        statuses = dict(check_nodes(graph, store, sorted(cited)))
        claims = {i for i in cited if graph.node(i).kind is NodeKind.CLAIM}
        if claims:
            claim_nodes = [graph.node(i) for i in sorted(claims)]
            behind = check_attempts(graph, store, attempts_behind(graph, claim_nodes))
            report = SourceReport(statuses=behind, claim_support={}, diagnostics=())
            assessed = assess_claims(
                graph, report, reviews or [], current, outcomes or [], only=claims, today=today
            )
            claim_status = {cid: a.status for cid, a in assessed.items()}
    assessments = assess_decisions(
        listing.decisions,
        node_ids=node_ids,
        source_statuses=statuses if graph is not None else None,
        claim_status=claim_status,
        file_hashes=hashes,
        fingerprint=current.fingerprint if current is not None else None,
        outcomes=outcomes or [],
        today=today,
    )
    return DecisionsView(listing, assessments, graph is not None, root is not None)


def history_of(listing: DecisionListing, decision_id: str) -> list[Decision]:
    """The decision and every decision connected to it by supersession, in either direction."""
    by_id = {d.decision_id: d for d in listing.decisions}
    if decision_id not in by_id:
        return []
    neighbours: dict[str, set[str]] = {}
    for d in listing.decisions:
        if d.supersedes is not None and d.supersedes in by_id:
            neighbours.setdefault(d.decision_id, set()).add(d.supersedes)
            neighbours.setdefault(d.supersedes, set()).add(d.decision_id)
    seen = {decision_id}
    queue = [decision_id]
    while queue:
        for nxt in sorted(neighbours.get(queue.pop(), ())):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return [by_id[i] for i in sorted(seen)]


def matching_links(listing: DecisionListing, kind: LinkKind, value: str) -> list[Decision]:
    """Decisions with a link of this kind to this value (a commit matches by prefix, 7+ chars)."""
    by_id = {d.decision_id: d for d in listing.decisions}
    found: dict[str, Decision] = {}
    for link in listing.links:
        if link.kind is not kind or link.decision_id not in by_id:
            continue
        same = link.value == value
        if kind is LinkKind.COMMIT and not same and min(len(value), len(link.value)) >= 7:
            same = link.value.startswith(value) or value.startswith(link.value)
        if same:
            found[link.decision_id] = by_id[link.decision_id]
    return sorted(found.values(), key=lambda d: (d.created_at, d.decision_id))


def decision_payload(
    decision: Decision, assessment: DecisionAssessment | None, links: Sequence[DecisionLink]
) -> dict[str, object]:
    """The JSON for one decision, shared by the CLI (and later the UI)."""
    return {
        "decision": decision.model_dump(mode="json"),
        "links": [link.model_dump(mode="json") for link in links],
        "assessment": assessment.model_dump(mode="json") if assessment is not None else None,
    }
