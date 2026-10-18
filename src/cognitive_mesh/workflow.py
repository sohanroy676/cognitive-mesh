"""Prepare the recalled context for one task (ADR-0017).

Recall needs no model provider. A first session has no index or graph yet, which is not an error:
the task then runs with an explicit empty pack. A file that exists but cannot be used still raises,
so a damaged memory is never mistaken for an empty one.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final

from cognitive_mesh.agents.context import NO_PRIOR_CONTEXT, RecalledContext
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.current import CurrentSource
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.outcomes import OutcomeStore
from cognitive_mesh.experience.reviews import ReviewStore
from cognitive_mesh.graph import DependencyGraph
from cognitive_mesh.identity import AppIdentity
from cognitive_mesh.ingestion import IngestionResult
from cognitive_mesh.retrieval import GatedContext, RecallResult, build_context, recall
from cognitive_mesh.traces import TraceStore, source_fingerprint
from cognitive_mesh.validity import current_environment

if TYPE_CHECKING:
    from cognitive_mesh.memory import Embedder

MAX_REQUEST_CHARS: Final = 2000
MAX_K: Final = 20  # at most this many hits per task, so the pack stays small


@dataclass(frozen=True)
class PreparedContext:
    gated: GatedContext
    recall: RecallResult | None  # None: nothing was searched
    not_searched: tuple[str, ...]  # why, when nothing was searched; printed for the person


def validate_request(request: str) -> None:
    """The request is the recall query and is recorded only as a length and a hash."""
    if not request.strip():
        raise ValueError("the request is empty")
    if len(request) > MAX_REQUEST_CHARS:
        raise ValueError(f"the request is longer than {MAX_REQUEST_CHARS} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in request):
        raise ValueError("the request contains control characters")


def prepare_context(
    request: str,
    *,
    ingestion: IngestionResult,
    graph: DependencyGraph,
    store: TraceStore,
    index_dir: Path,
    graph_file: Path,
    reviews_file: Path | None,
    make_embedder: "Callable[[], Embedder]",
    k: int,
    min_similarity: float,
    identity: AppIdentity | None = None,
    today: date | None = None,
    outcomes_file: Path | None = None,
) -> PreparedContext:
    fingerprint = source_fingerprint(ingestion)
    missing = tuple(
        f"{what} does not exist"
        for what, present in (
            ("the memory index", index_dir.is_dir()),
            ("the experience graph file", graph_file.is_file()),
        )
        if not present
    )
    if missing:
        pack = RecalledContext(
            message=NO_PRIOR_CONTEXT,
            query_sha256=hashlib.sha256(request.encode("utf-8")).hexdigest(),
            current_fingerprint=fingerprint,
            k=k,
            min_similarity=min_similarity,
        )
        return PreparedContext(GatedContext(pack=pack, not_applicable=()), None, missing)

    # Imported here so the commands that never recall do not load chromadb and torch.
    from cognitive_mesh.memory import MemoryIndex

    loaded = graph_store.load(graph_file)
    reviews = []
    if reviews_file is not None:
        with ReviewStore.open_existing(reviews_file) as review_store:
            reviews = review_store.list_reviews()
    outcomes = []
    if outcomes_file is not None:
        with OutcomeStore.open_existing(outcomes_file) as outcome_store:
            outcomes = outcome_store.list_outcomes()
    current = CurrentSource(fingerprint, graph)
    index = MemoryIndex.open_existing(index_dir, make_embedder())
    found = recall(
        request,
        index,
        store,
        loaded.snapshot,
        reviews=reviews,
        outcomes=outcomes,
        current=current,
        k=k,
        min_similarity=min_similarity,
        today=today,
        environment=current_environment(),
    )
    gated = build_context(
        request, found, ExperienceGraph(loaded.snapshot), graph, store, ingestion.root, identity
    )
    return PreparedContext(gated, found, ())
