"""Records for evidence-aware retrieval (ADR-0016)."""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.experience.assess import ClaimAssessment
from cognitive_mesh.experience.decision_assess import DecisionAssessment
from cognitive_mesh.experience.models import REPETITION_NOTE
from cognitive_mesh.memory.models import SIMILARITY_NOTE, UnverifiedDecisionHit

RAW_LABEL: Final = "raw trace: a record of what happened, not validated knowledge"
VALIDATION_NOTE: Final = (
    "validated means the claim holds for one named code version by a stated method (re-derivation "
    "by the same analyzer, or a person's review); it is not proof that the claim is true"
)
RETRIEVAL_NOTES: Final = (SIMILARITY_NOTE, REPETITION_NOTE, VALIDATION_NOTE)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievedVia(_Frozen):
    """Which search hit led to a claim. Similarity says how the query matched, never how true."""

    doc_id: str
    similarity: float


class RecalledClaim(_Frozen):
    assessment: ClaimAssessment
    retrieved_via: tuple[RetrievedVia, ...]


class RawExperience(_Frozen):
    """A hit that states no claim. Always labelled raw."""

    run_id: str
    step_id: str
    attempt_no: int
    capability: str
    result_status: str
    basis: str | None
    kind: Literal["failed", "model_unverified", "no_claims"]
    similarity: float
    excerpt: str  # start of the trace document, untrusted data
    label: str = RAW_LABEL


class Unconfirmed(_Frozen):
    """A hit that cannot be traced to a record both stores agree on. Never offered as support."""

    run_id: str
    step_id: str
    attempt_no: int
    similarity: float
    reason: str  # source_missing | source_changed | source_unreadable | not_in_graph


class RecalledDecision(_Frozen):
    """A decision found by meaning (`similarity`) or because it cites something recalled."""

    assessment: DecisionAssessment
    similarity: float | None = None  # None: found only through the graph
    excerpt: str = ""  # start of the embedded text, untrusted data
    cites_recalled: tuple[str, ...] = ()  # recalled graph ids this decision cites


class DecisionRecall(_Frozen):
    indexed: bool  # False: the index has no decisions collection
    indexed_documents: int = 0
    dropped_weak: int = 0
    decisions: tuple[RecalledDecision, ...] = ()
    unconfirmed: tuple[UnverifiedDecisionHit, ...] = ()
    message: str = ""


class RecallResult(_Frozen):
    query_length: int
    k: int
    min_similarity: float
    indexed_documents: int
    dropped_weak: int
    current_fingerprint: str | None  # None: no --root, so currency is unknown
    subjects_followed: int = 0  # ADR-0019: only claims about these subjects were assessed
    claims_assessed: int = 0
    claims: tuple[RecalledClaim, ...]
    experiences_without_claims: tuple[RawExperience, ...]
    unconfirmed: tuple[Unconfirmed, ...]
    message: str
    decisions: DecisionRecall | None = None  # ADR-0039; None: not asked
    notes: tuple[str, ...] = RETRIEVAL_NOTES
