"""Records for semantic memory retrieval (ADR-0014)."""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

SIMILARITY_NOTE: Final = (
    "Similarity measures closeness in meaning to the query. It is not evidence that a past "
    "result was correct. Check the supporting trace record before relying on a hit."
)


class MemoryIndexError(Exception):
    """The index or the embedding model is unusable. Messages are fixed text: never document text,
    the query, vectors or exception text."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelKey(_Frozen):
    """Identifies the embedding space. Vectors from different keys are never mixed."""

    name: str
    revision: str
    dimension: int = Field(gt=0)
    text_format: int = Field(gt=0)


class MemoryDocument(_Frozen):
    """One eligible experience: the final attempt of one step of a finished run."""

    run_id: str
    step_id: str
    attempt_no: int
    capability: str
    result_status: str  # "success" or "failure" (the final attempt)
    basis: str | None  # "deterministic" or "model_unverified" for a success, else None
    text: str
    text_sha256: str

    @property
    def doc_id(self) -> str:
        return f"{self.run_id}/{self.step_id}/{self.attempt_no}"


class IndexReport(_Frozen):
    added: int
    updated: int
    unchanged: int
    orphaned: int  # indexed documents whose source is no longer eligible or present; kept
    malformed_runs: tuple[str, ...]  # run ids that could not be read, never hidden


class SourceCheck(StrEnum):
    OK = "ok"
    MISSING = "source_missing"  # the trace row is gone
    CHANGED = "source_changed"  # the row now yields different text than was embedded
    UNREADABLE = "source_unreadable"  # the run is malformed in the trace database


class MemoryHit(_Frozen):
    run_id: str
    step_id: str
    attempt_no: int
    capability: str
    result_status: str
    basis: str | None
    similarity: float
    excerpt: str  # start of the document rebuilt from the trace database, untrusted data


class UnverifiedHit(_Frozen):
    """A match whose source could not be confirmed. Never presented as a supporting record."""

    run_id: str
    step_id: str
    attempt_no: int
    similarity: float
    reason: SourceCheck


class SearchResult(_Frozen):
    query_length: int
    k: int
    min_similarity: float
    hits: tuple[MemoryHit, ...]
    unverified: tuple[UnverifiedHit, ...]
    dropped_weak: int
    indexed_documents: int
    message: str
    note: str = SIMILARITY_NOTE
