"""Semantic retrieval over stored run traces (v0.7.0, ADR-0014)."""

from cognitive_mesh.memory.documents import TEXT_FORMAT_VERSION, eligible_documents
from cognitive_mesh.memory.embedder import Embedder, SentenceTransformerEmbedder
from cognitive_mesh.memory.index import MemoryIndex
from cognitive_mesh.memory.models import (
    SIMILARITY_NOTE,
    IndexReport,
    MemoryDocument,
    MemoryHit,
    MemoryIndexError,
    ModelKey,
    SearchResult,
    SourceCheck,
    UnverifiedHit,
)

__all__ = [
    "SIMILARITY_NOTE",
    "TEXT_FORMAT_VERSION",
    "Embedder",
    "IndexReport",
    "MemoryDocument",
    "MemoryHit",
    "MemoryIndex",
    "MemoryIndexError",
    "ModelKey",
    "SearchResult",
    "SentenceTransformerEmbedder",
    "SourceCheck",
    "UnverifiedHit",
    "eligible_documents",
]
