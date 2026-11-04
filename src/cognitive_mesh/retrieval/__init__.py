"""Evidence-aware retrieval over the vector index and the experience graph (v0.9.0, ADR-0016)."""

from cognitive_mesh.retrieval.context import GatedContext, NotApplicable, build_context, root_name
from cognitive_mesh.retrieval.decisions import recall_decisions, recall_decisions_for
from cognitive_mesh.retrieval.models import (
    RAW_LABEL,
    RETRIEVAL_NOTES,
    VALIDATION_NOTE,
    DecisionRecall,
    RawExperience,
    RecalledClaim,
    RecalledDecision,
    RecallResult,
    RetrievedVia,
    Unconfirmed,
)
from cognitive_mesh.retrieval.recall import NOT_IN_GRAPH, Searcher, recall

__all__ = [
    "NOT_IN_GRAPH",
    "RAW_LABEL",
    "RETRIEVAL_NOTES",
    "VALIDATION_NOTE",
    "DecisionRecall",
    "GatedContext",
    "NotApplicable",
    "RawExperience",
    "RecallResult",
    "RecalledClaim",
    "RecalledDecision",
    "RetrievedVia",
    "Searcher",
    "Unconfirmed",
    "build_context",
    "recall",
    "recall_decisions",
    "recall_decisions_for",
    "root_name",
]
