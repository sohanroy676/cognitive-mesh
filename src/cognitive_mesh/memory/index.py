"""The persistent vector index (ADR-0014). The only module that imports `chromadb`.

The SQLite trace database stays authoritative; this index is a derived copy. Chroma never embeds:
vectors come from the `Embedder` we pass in. Messages never contain document text or the query.
"""

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

import chromadb
from chromadb.config import Settings

from cognitive_mesh.memory.decision_documents import (
    DECISION_TEXT_FORMAT_VERSION,
    decision_document,
    verify_decision,
)
from cognitive_mesh.memory.documents import eligible_documents, verify_source
from cognitive_mesh.memory.embedder import BATCH_SIZE, Embedder
from cognitive_mesh.memory.models import (
    DecisionDocument,
    DecisionHit,
    DecisionIndexReport,
    DecisionSearchResult,
    IndexReport,
    MemoryDocument,
    MemoryHit,
    MemoryIndexError,
    ModelKey,
    SearchResult,
    SourceCheck,
    UnverifiedDecisionHit,
    UnverifiedHit,
)
from cognitive_mesh.traces import (
    TraceRecordError,
    TraceStore,
)

COLLECTION: Final = "experiences"
DECISIONS_COLLECTION: Final = "decisions"  # ADR-0039: own text format and check
MAX_QUERY_CHARS: Final = 2000
MAX_K: Final = 50
EXCERPT_CHARS: Final = 300
_DB_FILE: Final = "chroma.sqlite3"
_BACKOFF_S: Final = (0.25, 0.5, 1.0, 2.0)  # waits before the 2nd to 5th try
_sleep = time.sleep  # tests replace this so they never wait


def _metadata_for(key: ModelKey) -> dict[str, str | int]:
    return {
        "hnsw:space": "cosine",
        "model_name": key.name,
        "model_revision": key.revision,
        "dimension": key.dimension,
        "text_format": key.text_format,
    }


def _decision_key(key: ModelKey) -> ModelKey:
    return key.model_copy(update={"text_format": DECISION_TEXT_FORMAT_VERSION})


def _check_compatible(collection: Any, key: ModelKey) -> None:
    meta = collection.metadata or {}
    expected = _metadata_for(key)
    if any(meta.get(name) != value for name, value in expected.items()):
        raise MemoryIndexError(
            "the index was built with a different embedding model or text format; "
            "build a new index in another directory"
        )


def _retry[T](action: Callable[[], T], *, only_locked: bool) -> T:
    """Run `action`, trying again after a wait while another process is using Chroma's files.

    Opening and upserting are safe to repeat. With `only_locked`, an error that does not say the
    database is locked is raised at once. After `_BACKOFF_S` is used up the last error is raised.
    """
    for delay in _BACKOFF_S:
        try:
            return action()
        except Exception as exc:
            if only_locked and "locked" not in str(exc).lower():
                raise
            _sleep(delay)
    return action()


def _open_client(path: Path) -> Any:
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def _client(path: Path) -> Any:
    # Observed when several processes create one new index at once: "database is locked", "table
    # collections already exists", and then, in the process that failed, an AttributeError from the
    # half-built cached client on the next try. All of them pass on a later try, so any error is
    # retried here (a few seconds at most before the usual message).
    try:
        return _retry(lambda: _open_client(path), only_locked=False)
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
    def __init__(self, collection: Any, embedder: Embedder, decisions: Any | None = None) -> None:
        self._col = collection
        self._embedder = embedder
        self._decisions = decisions  # None: an index built before decisions existed

    @classmethod
    def open_for_write(cls, path: Path, embedder: Embedder) -> "MemoryIndex":
        """Open for indexing; creates the directory and collection if they are new."""
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise MemoryIndexError("cannot create the memory index directory") from None
        client = _client(path)
        try:
            collection = _retry(
                lambda: client.get_or_create_collection(
                    COLLECTION, metadata=_metadata_for(embedder.key), embedding_function=None
                ),
                only_locked=False,
            )
        except Exception:
            raise MemoryIndexError("cannot open the memory index") from None
        _check_compatible(collection, embedder.key)
        dkey = _decision_key(embedder.key)
        try:
            decisions = _retry(
                lambda: client.get_or_create_collection(
                    DECISIONS_COLLECTION, metadata=_metadata_for(dkey), embedding_function=None
                ),
                only_locked=False,
            )
        except Exception:
            raise MemoryIndexError("cannot open the memory index") from None
        _check_compatible(decisions, dkey)
        return cls(collection, embedder, decisions)

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
        decisions: Any | None = None
        try:
            decisions = client.get_collection(DECISIONS_COLLECTION, embedding_function=None)
        except Exception:  # an index built before decisions existed has none: not an error
            decisions = None
        if decisions is not None:
            _check_compatible(decisions, _decision_key(embedder.key))
        return cls(collection, embedder, decisions)

    @property
    def has_decisions(self) -> bool:
        """True when this index has a decisions collection (it may still be empty)."""
        return self._decisions is not None

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
            _retry(
                lambda: self._col.upsert(
                    ids=[d.doc_id for d in batch],
                    embeddings=vectors,
                    metadatas=[_doc_metadata(d) for d in batch],
                ),
                only_locked=True,
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

    # -- decisions (ADR-0039) --------------------------------------------------------------

    def index_decisions(self, store: TraceStore) -> DecisionIndexReport:
        """Embed every readable decision that is new or changed. Safe to repeat."""
        if self._decisions is None:
            raise MemoryIndexError("this index has no decisions collection")
        listing = store.read_decisions()
        docs: dict[str, DecisionDocument] = {
            d.decision_id: decision_document(d) for d in listing.decisions
        }
        known = self._known_decision_hashes(list(docs))
        todo = [d for d in docs.values() if known.get(d.decision_id) != d.text_sha256]
        added = sum(1 for d in todo if d.decision_id not in known)
        for start in range(0, len(todo), BATCH_SIZE):
            self._upsert_decisions(todo[start : start + BATCH_SIZE])
        try:
            all_ids = set(self._decisions.get(include=[])["ids"])
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None
        return DecisionIndexReport(
            added=added,
            updated=len(todo) - added,
            unchanged=len(docs) - len(todo),
            orphaned=len(all_ids - set(docs)),
            malformed=tuple(sorted(listing.malformed)),
        )

    def _known_decision_hashes(self, ids: Sequence[str]) -> dict[str, str]:
        known: dict[str, str] = {}
        try:
            for start in range(0, len(ids), 500):
                got = self._decisions.get(  # type: ignore[union-attr]
                    ids=list(ids[start : start + 500]), include=["metadatas"]
                )
                for doc_id, meta in zip(got["ids"], got["metadatas"], strict=True):
                    known[doc_id] = str((meta or {}).get("text_sha256", ""))
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None
        return known

    def _upsert_decisions(self, batch: Sequence[DecisionDocument]) -> None:
        vectors = self._embedder.embed([d.text for d in batch])
        dimension = self._embedder.key.dimension
        if len(vectors) != len(batch) or any(len(v) != dimension for v in vectors):
            raise MemoryIndexError("the embedder returned vectors of the wrong size")
        try:
            _retry(
                lambda: self._decisions.upsert(  # type: ignore[union-attr]
                    ids=[d.decision_id for d in batch],
                    embeddings=vectors,
                    metadatas=[
                        {"text_sha256": d.text_sha256, "basis": d.basis, "app_id": d.app_id or ""}
                        for d in batch
                    ],
                ),
                only_locked=True,
            )
        except Exception:
            raise MemoryIndexError("cannot write to the memory index") from None

    def search_decisions(
        self, query: str, store: TraceStore, *, k: int, min_similarity: float
    ) -> DecisionSearchResult:
        """Like `search`, over decisions. Each hit is rebuilt from its trace row or set aside."""
        if self._decisions is None:
            raise MemoryIndexError("this index has no decisions collection")
        text = query.strip()
        if not text:
            raise ValueError("the query is empty")
        if len(text) > MAX_QUERY_CHARS:
            raise ValueError(f"the query is longer than {MAX_QUERY_CHARS} characters")
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be between 1 and {MAX_K}")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between 0 and 1")
        try:
            total = int(self._decisions.count())
        except Exception:
            raise MemoryIndexError("cannot read the memory index") from None

        def result(
            hits: tuple[DecisionHit, ...] = (),
            unverified: tuple[UnverifiedDecisionHit, ...] = (),
            dropped: int = 0,
            message: str = "",
        ) -> DecisionSearchResult:
            return DecisionSearchResult(
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
            return result(message="no decisions are indexed; run `memory index` first")
        vector = self._embedder.embed([text])
        if len(vector) != 1 or len(vector[0]) != self._embedder.key.dimension:
            raise MemoryIndexError("the embedder returned vectors of the wrong size")
        try:
            got = self._decisions.query(
                query_embeddings=vector,
                n_results=min(k, total),
                include=["metadatas", "distances"],
            )
            ids = got["ids"][0]
            metas = got["metadatas"][0]
            distances = got["distances"][0]
        except Exception:
            raise MemoryIndexError("cannot search the memory index") from None

        listing = store.read_decisions()
        hits: list[DecisionHit] = []
        unverified: list[UnverifiedDecisionHit] = []
        dropped = 0
        for decision_id, meta, distance in zip(ids, metas, distances, strict=True):
            similarity = round(max(-1.0, min(1.0, 1.0 - float(distance))), 4)
            if similarity < min_similarity:
                dropped += 1
                continue
            try:
                expected = str(meta["text_sha256"])
            except (KeyError, TypeError):
                raise MemoryIndexError("the memory index holds a malformed entry") from None
            doc, check = verify_decision(listing, str(decision_id), expected)
            if doc is None or check is not SourceCheck.OK:
                unverified.append(
                    UnverifiedDecisionHit(
                        decision_id=str(decision_id), similarity=similarity, reason=check
                    )
                )
                continue
            hits.append(
                DecisionHit(
                    decision_id=doc.decision_id,
                    app_id=doc.app_id,
                    basis=doc.basis,
                    summary=doc.summary,
                    similarity=similarity,
                    excerpt=doc.text[:EXCERPT_CHARS],
                )
            )
        message = ""
        if not hits:
            message = (
                "no sufficiently similar decision"
                if not unverified
                else "no similar decision with a confirmed source"
            )
        return result(tuple(hits), tuple(unverified), dropped, message)
