"""The persistent vector index (ADR-0014). The only module that imports `chromadb`.

The SQLite trace database stays authoritative; this index is a derived copy. Chroma never embeds:
vectors come from the `Embedder` we pass in. Messages never contain document text or the query.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import chromadb
from chromadb.config import Settings

from cognitive_mesh.memory.documents import eligible_documents, verify_source
from cognitive_mesh.memory.embedder import BATCH_SIZE, Embedder
from cognitive_mesh.memory.models import (
    IndexReport,
    MemoryDocument,
    MemoryHit,
    MemoryIndexError,
    ModelKey,
    SearchResult,
    SourceCheck,
    UnverifiedHit,
)
from cognitive_mesh.traces import (
    TraceRecordError,
    TraceStore,
)

COLLECTION: Final = "experiences"
MAX_QUERY_CHARS: Final = 2000
MAX_K: Final = 50
EXCERPT_CHARS: Final = 300
_DB_FILE: Final = "chroma.sqlite3"


def _metadata_for(key: ModelKey) -> dict[str, str | int]:
    return {
        "hnsw:space": "cosine",
        "model_name": key.name,
        "model_revision": key.revision,
        "dimension": key.dimension,
        "text_format": key.text_format,
    }


def _check_compatible(collection: Any, key: ModelKey) -> None:
    meta = collection.metadata or {}
    expected = _metadata_for(key)
    if any(meta.get(name) != value for name, value in expected.items()):
        raise MemoryIndexError(
            "the index was built with a different embedding model or text format; "
            "build a new index in another directory"
        )


def _client(path: Path) -> Any:
    try:
        return chromadb.PersistentClient(
            path=str(path), settings=Settings(anonymized_telemetry=False)
        )
    except Exception:
        raise MemoryIndexError("cannot open the memory index") from None


def _doc_metadata(doc: MemoryDocument) -> dict[str, str | int]:
    return {
        "run_id": doc.run_id,
        "step_id": doc.step_id,
        "attempt_no": doc.attempt_no,
        "capability": doc.capability,
        "result_status": doc.result_status,
        "basis": doc.basis or "",
        "text_sha256": doc.text_sha256,
    }


class MemoryIndex:
    def __init__(self, collection: Any, embedder: Embedder) -> None:
        self._col = collection
        self._embedder = embedder

    @classmethod
    def open_for_write(cls, path: Path, embedder: Embedder) -> "MemoryIndex":
        """Open for indexing; creates the directory and collection if they are new."""
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise MemoryIndexError("cannot create the memory index directory") from None
        client = _client(path)
        try:
            collection = client.get_or_create_collection(
                COLLECTION, metadata=_metadata_for(embedder.key), embedding_function=None
            )
        except Exception:
            raise MemoryIndexError("cannot open the memory index") from None
        _check_compatible(collection, embedder.key)
        return cls(collection, embedder)

    @classmethod
    def open_existing(cls, path: Path, embedder: Embedder) -> "MemoryIndex":
        """Open for searching. Never creates anything."""
        if not path.is_dir() or not (path / _DB_FILE).is_file():
            raise MemoryIndexError("the memory index does not exist")
        client = _client(path)
        try:
            collection = client.get_collection(COLLECTION, embedding_function=None)
        except Exception:
            raise MemoryIndexError("the directory is not a memory index") from None
        _check_compatible(collection, embedder.key)
        return cls(collection, embedder)

    def count(self) -> int:
        try:
            return int(self._col.count())
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None

    # -- indexing --------------------------------------------------------------------------

    def index_traces(self, store: TraceStore) -> IndexReport:
        """Embed every eligible document that is new or changed. Safe to repeat."""
        listing = store.list()
        docs: dict[str, MemoryDocument] = {}
        malformed = list(listing.malformed)
        for summary in listing.runs:
            try:
                trace = store.get(summary.run_id)
            except TraceRecordError:
                malformed.append(summary.run_id)
                continue
            for doc in eligible_documents(trace):
                docs[doc.doc_id] = doc

        known = self._known_hashes(list(docs))
        todo = [d for d in docs.values() if known.get(d.doc_id) != d.text_sha256]
        added = sum(1 for d in todo if d.doc_id not in known)
        for start in range(0, len(todo), BATCH_SIZE):
            self._upsert(todo[start : start + BATCH_SIZE])
        try:
            all_ids = set(self._col.get(include=[])["ids"])
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None
        return IndexReport(
            added=added,
            updated=len(todo) - added,
            unchanged=len(docs) - len(todo),
            orphaned=len(all_ids - set(docs)),
            malformed_runs=tuple(sorted(set(malformed))),
        )

    def _known_hashes(self, ids: Sequence[str]) -> dict[str, str]:
        known: dict[str, str] = {}
        try:
            for start in range(0, len(ids), 500):
                got = self._col.get(ids=list(ids[start : start + 500]), include=["metadatas"])
                for doc_id, meta in zip(got["ids"], got["metadatas"], strict=True):
                    known[doc_id] = str((meta or {}).get("text_sha256", ""))
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None
        return known

    def _upsert(self, batch: Sequence[MemoryDocument]) -> None:
        vectors = self._embedder.embed([d.text for d in batch])
        dimension = self._embedder.key.dimension
        if len(vectors) != len(batch) or any(len(v) != dimension for v in vectors):
            raise MemoryIndexError("the embedder returned vectors of the wrong size")
        try:
            self._col.upsert(
                ids=[d.doc_id for d in batch],
                embeddings=vectors,
                metadatas=[_doc_metadata(d) for d in batch],
            )
        except Exception:
            raise MemoryIndexError("cannot write to the memory index") from None

    # -- searching -------------------------------------------------------------------------

    def search(
        self, query: str, store: TraceStore, *, k: int, min_similarity: float
    ) -> SearchResult:
        text = query.strip()
        if not text:
            raise ValueError("the query is empty")
        if len(text) > MAX_QUERY_CHARS:
            raise ValueError(f"the query is longer than {MAX_QUERY_CHARS} characters")
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be between 1 and {MAX_K}")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between 0 and 1")

        total = self.count()

        def result(
            hits: tuple[MemoryHit, ...] = (),
            unverified: tuple[UnverifiedHit, ...] = (),
            dropped: int = 0,
            message: str = "",
        ) -> SearchResult:
            return SearchResult(
                query_length=len(text),
                k=k,
                min_similarity=min_similarity,
                hits=hits,
                unverified=unverified,
                dropped_weak=dropped,
                indexed_documents=total,
                message=message,
            )

        if total == 0:
            return result(message="the index is empty; run `memory index` first")
        vector = self._embedder.embed([text])
        if len(vector) != 1 or len(vector[0]) != self._embedder.key.dimension:
            raise MemoryIndexError("the embedder returned vectors of the wrong size")
        try:
            got = self._col.query(
                query_embeddings=vector,
                n_results=min(k, total),
                include=["metadatas", "distances"],
            )
            metas = got["metadatas"][0]
            distances = got["distances"][0]
        except Exception:
            raise MemoryIndexError("cannot search the memory index") from None

        hits: list[MemoryHit] = []
        unverified: list[UnverifiedHit] = []
        dropped = 0
        sources: dict[str, dict[str, MemoryDocument] | None] = {}
        for meta, distance in zip(metas, distances, strict=True):
            similarity = round(max(-1.0, min(1.0, 1.0 - float(distance))), 4)
            if similarity < min_similarity:
                dropped += 1
                continue
            try:
                run_id, step_id = str(meta["run_id"]), str(meta["step_id"])
                attempt_no, expected = int(meta["attempt_no"]), str(meta["text_sha256"])
            except (KeyError, TypeError, ValueError):
                raise MemoryIndexError("the memory index holds a malformed entry") from None
            doc, check = verify_source(store, sources, run_id, step_id, attempt_no, expected)
            if doc is None or check is not SourceCheck.OK:
                unverified.append(
                    UnverifiedHit(
                        run_id=run_id,
                        step_id=step_id,
                        attempt_no=attempt_no,
                        similarity=similarity,
                        reason=check,
                    )
                )
                continue
            hits.append(
                MemoryHit(
                    run_id=run_id,
                    step_id=step_id,
                    attempt_no=attempt_no,
                    capability=doc.capability,
                    result_status=doc.result_status,
                    basis=doc.basis,
                    similarity=similarity,
                    excerpt=doc.text[:EXCERPT_CHARS],
                )
            )
        message = ""
        if not hits:
            message = (
                "no sufficiently similar experience"
                if not unverified
                else "no similar experience with a confirmed source"
            )
        return result(tuple(hits), tuple(unverified), dropped, message)
