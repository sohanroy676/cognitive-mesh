"""Recall of decisions (ADR-0039): found by meaning, or because they cite something recalled.

The vector search only decides which decisions are looked at. Status comes from the assessment
(`experience/decision_assess.py`); similarity never changes it. Reasoning text of a model-written
decision is shown labelled as unverified.
"""

from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Protocol

from cognitive_mesh.experience.decision_assess import DecisionAssessment
from cognitive_mesh.experience.decision_view import assess_all
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import EdgeKind, Snapshot
from cognitive_mesh.experience.outcomes import Outcome
from cognitive_mesh.experience.reviews import Review
from cognitive_mesh.memory.models import DecisionSearchResult, UnverifiedDecisionHit
from cognitive_mesh.retrieval.models import DecisionRecall, RecalledDecision, RecallResult
from cognitive_mesh.traces import TraceStore

NOT_INDEXED = "this index has no decisions collection; run `memory index` to add one"


class DecisionSearcher(Protocol):
    @property
    def has_decisions(self) -> bool: ...

    def search_decisions(
        self, query: str, store: TraceStore, *, k: int, min_similarity: float
    ) -> DecisionSearchResult: ...


def recall_decisions(
    query: str,
    index: DecisionSearcher,
    store: TraceStore,
    graph: ExperienceGraph | None,
    recalled_ids: Collection[str],
    assessments: Mapping[str, DecisionAssessment],
    *,
    k: int,
    min_similarity: float,
) -> DecisionRecall:
    """Decisions matching `query`, plus decisions that cite any of `recalled_ids` in the graph."""
    found: dict[str, RecalledDecision] = {}
    unverified: tuple[UnverifiedDecisionHit, ...] = ()
    dropped = 0
    indexed_documents = 0
    message = ""
    if index.has_decisions:
        result = index.search_decisions(query, store, k=k, min_similarity=min_similarity)
        indexed_documents = result.indexed_documents
        dropped = result.dropped_weak
        unverified = result.unverified
        message = result.message
        for hit in result.hits:
            assessed = assessments.get(hit.decision_id)
            if assessed is None:  # the row was readable a moment ago; do not offer it unassessed
                continue
            found[hit.decision_id] = RecalledDecision(
                assessment=assessed, similarity=hit.similarity, excerpt=hit.excerpt
            )
    else:
        message = NOT_INDEXED
    if graph is not None:
        for rid in sorted(recalled_ids):
            if not graph.has_node(rid):
                continue
            for edge in graph.in_edges(rid, {EdgeKind.CITES}):
                assessed = assessments.get(edge.source)
                if assessed is None:
                    continue
                old = found.get(edge.source)
                cited = tuple(sorted({*(old.cites_recalled if old else ()), rid}))
                found[edge.source] = RecalledDecision(
                    assessment=assessed,
                    similarity=old.similarity if old else None,
                    excerpt=old.excerpt if old else "",
                    cites_recalled=cited,
                )
    ordered = tuple(
        sorted(
            found.values(),
            key=lambda d: (
                d.similarity is None,
                -(d.similarity or 0.0),
                d.assessment.decision_id,
            ),
        )
    )
    if not ordered and not message:
        message = "no sufficiently similar decision"
    return DecisionRecall(
        indexed=index.has_decisions,
        indexed_documents=indexed_documents,
        dropped_weak=dropped,
        decisions=ordered,
        unconfirmed=tuple(unverified),
        message=message,
    )


def recall_decisions_for(
    found: RecallResult,
    query: str,
    index: DecisionSearcher,
    store: TraceStore,
    snapshot: Snapshot,
    *,
    reviews: list[Review] | None,
    outcomes: list[Outcome] | None,
    root: Path | None,
    k: int,
    min_similarity: float,
    today: date,
) -> DecisionRecall:
    """Decisions for a `recall` (ADR-0039), shared by the CLI and the UI so both say the same.

    Every readable decision is assessed (cited graph nodes, files under `root`, outcomes); the
    ones returned are those the query matches by meaning and those citing what was recalled.
    """
    graph = ExperienceGraph(snapshot)
    view = assess_all(
        store, graph=graph, reviews=reviews, outcomes=outcomes, root=root, today=today
    )
    recalled = {c.assessment.claim_id for c in found.claims}
    for c in found.claims:
        recalled.update(f"attempt:{v.doc_id}" for v in c.retrieved_via)
    recalled.update(
        f"attempt:{r.run_id}/{r.step_id}/{r.attempt_no}" for r in found.experiences_without_claims
    )
    return recall_decisions(
        query,
        index,
        store,
        graph,
        recalled,
        view.assessments,
        k=k,
        min_similarity=min_similarity,
    )
