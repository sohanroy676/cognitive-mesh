"""What the memory-on arm gives the coder, and how it grows between tasks (ADR-0043).

Each arm has its own trace database, index, graph file, outcomes file and worktrees, so an arm can
see only its own earlier tasks, and a repeat never sees another repeat. The memory-off arm never
calls anything in this module.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from cognitive_mesh.agents.context import PastChange, RecalledContext
from cognitive_mesh.change.past import past_changes_from_hits
from cognitive_mesh.experience import build_from_traces
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.identity import derive_identity
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.workflow import PROFILE_DEFAULTS, prepare_context
from cognitive_mesh.workspace.access import WorkspaceAccess

if TYPE_CHECKING:
    from cognitive_mesh.memory import Embedder

PAST_K: Final = 10  # decisions searched; at most MAX_PAST_CHANGES of them are change drafts offered


@dataclass(frozen=True)
class ArmPaths:
    """Where one arm of one repeat keeps its files."""

    base: Path

    @property
    def trace(self) -> Path:
        return self.base / "trace.sqlite3"

    @property
    def index(self) -> Path:
        return self.base / "index"

    @property
    def graph(self) -> Path:
        return self.base / "graph.sqlite3"

    @property
    def outcomes(self) -> Path:
        return self.base / "outcomes.sqlite3"

    @property
    def worktrees(self) -> Path:
        return self.base / "worktrees"


def arm_paths(out: Path, repeat: int, arm: str) -> ArmPaths:
    return ArmPaths(out / f"r{repeat}" / arm)


def memory_for_task(
    request: str,
    *,
    access: WorkspaceAccess,
    store: TraceStore,
    paths: ArmPaths,
    embedder: "Embedder",
) -> tuple[RecalledContext, tuple[PastChange, ...]]:
    """The claims pack and the earlier change drafts this arm has for `request`.

    Before the arm's first task nothing exists yet: the pack is the explicit empty pack.
    """
    root = access.reads[access.current].root
    ingestion = ingest_application(root)
    k, floor = PROFILE_DEFAULTS["precision"]
    prepared = prepare_context(
        request,
        ingestion=ingestion,
        graph=build_dependency_graph(ingestion),
        store=store,
        index_dir=paths.index,
        graph_file=paths.graph,
        reviews_file=None,
        make_embedder=lambda: embedder,
        k=k,
        min_similarity=floor,
        identity=derive_identity(ingestion.root, None),
        outcomes_file=paths.outcomes if paths.outcomes.is_file() else None,
    )
    past: tuple[PastChange, ...] = ()
    if paths.index.is_dir():
        from cognitive_mesh.memory import MemoryIndex

        index = MemoryIndex.open_existing(paths.index, embedder)
        if index.has_decisions:
            found = index.search_decisions(request, store, k=PAST_K, min_similarity=floor)
            past = past_changes_from_hits(found, store)
    return prepared.gated.pack, past


def refresh_memory(store: TraceStore, paths: ArmPaths, embedder: "Embedder") -> None:
    """Index the arm's traces and decisions and rebuild its graph, so the next task can use them."""
    from cognitive_mesh.memory import MemoryIndex

    index = MemoryIndex.open_for_write(paths.index, embedder)
    index.index_traces(store)
    index.index_decisions(store)
    graph_store.save(paths.graph, build_from_traces(store))
