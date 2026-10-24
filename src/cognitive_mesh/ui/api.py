"""Read-only JSON views for the web UI (ADR-0031). Each route returns `(status, body)`.

Every store is opened inside the call and closed before it returns. Nothing here creates a
file or writes to one. Messages come from the library's fixed error strings.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cognitive_mesh import __version__
from cognitive_mesh.experience import (
    ExperienceGraph,
    ExperienceGraphError,
    ExperienceNodeNotFoundError,
    check_sources,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.assess import assess_claims, attempts_behind, orphaned_reviews
from cognitive_mesh.experience.current import observe_current
from cognitive_mesh.experience.history import build_history, empty_history, find_node
from cognitive_mesh.experience.models import CLAIM_NOTE, REPETITION_NOTE, NodeKind
from cognitive_mesh.experience.outcomes import OutcomeStore, OutcomeStoreError
from cognitive_mesh.experience.report import (
    check_payload,
    claims_payload,
    claims_view,
    scoped_node_view,
    show_payload,
)
from cognitive_mesh.experience.reviews import ReviewStore, ReviewStoreError
from cognitive_mesh.experience.verify import SourceReport, check_attempts
from cognitive_mesh.graph import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    build_dependency_graph,
    build_view,
    render_html,
)
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.memory import MemoryIndexError
from cognitive_mesh.traces import TraceNotFoundError, TraceStore, TraceStoreError
from cognitive_mesh.ui.config import EmbedderHolder, UiConfig
from cognitive_mesh.ui.errors import ApiError
from cognitive_mesh.ui.graphdata import graph_payload
from cognitive_mesh.ui.paths import resolve_folder
from cognitive_mesh.validity import current_environment, utc_today

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from cognitive_mesh.experience.outcomes import Outcome
    from cognitive_mesh.experience.reviews import Review

Reply = tuple[int, dict[str, Any]]

NO_ROOT_NOTE = "no --root given: whether claims are current is unknown"


def error_reply(exc: Exception) -> Reply:
    """Map the exceptions the CLI maps to exit codes onto HTTP statuses. Unknown ones are opaque."""
    if isinstance(exc, ApiError):
        return exc.status, {"error": exc.message}
    if isinstance(exc, (TraceNotFoundError, ExperienceNodeNotFoundError)):
        return 404, {"error": str(exc)}
    if isinstance(
        exc,
        (
            TraceStoreError,
            ExperienceGraphError,
            MemoryIndexError,
            ReviewStoreError,
            OutcomeStoreError,
            IngestionError,
        ),
    ):
        return 409, {"error": str(exc)}
    if isinstance(exc, ValueError):
        return 400, {"error": str(exc)}
    return 500, {"error": "internal error"}


def _need(path: Path | None, option: str) -> Path:
    if path is None:
        raise ApiError(409, f"this view needs --{option}")
    return path


def _text(params: Mapping[str, str], name: str) -> str:
    value = params.get(name, "")
    if not value.strip():
        raise ValueError(f"the query parameter {name!r} is required")
    return value


def _flag(params: Mapping[str, str], name: str) -> bool:
    return params.get(name, "") in {"1", "true", "yes"}


def _number(params: Mapping[str, str], name: str, default: float, kind: type) -> Any:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


@dataclass
class UiApi:
    config: UiConfig
    embedder: EmbedderHolder
    # The embedding model and the Chroma client are not shared across threads (ADR-0031 point 10)
    _heavy: threading.Lock
    # One graph build at a time: ingestion is CPU and disk heavy (ADR-0032 point 2)
    _graph_lock: threading.Lock

    @classmethod
    def create(cls, config: UiConfig) -> UiApi:
        return cls(
            config, EmbedderHolder(config.embedder_factory), threading.Lock(), threading.Lock()
        )

    # -- inputs -------------------------------------------------------------------------

    def _reviews(self) -> list[Review]:
        if self.config.reviews is None:
            return []
        with ReviewStore.open_existing(self.config.reviews) as store:
            return store.list_reviews()

    def _outcomes(self) -> list[Outcome] | None:
        if self.config.outcomes is None:
            return None
        with OutcomeStore.open_existing(self.config.outcomes) as store:
            return store.list_outcomes()

    # -- routes -------------------------------------------------------------------------

    def status(self, params: Mapping[str, str]) -> Reply:
        cfg = self.config

        def file_state(path: Path | None, opener: Any = None) -> dict[str, Any]:
            if path is None:
                return {"given": False, "state": "not given"}
            if not path.exists():
                return {"given": True, "state": "missing"}
            if opener is not None:
                try:
                    opener(path).close()
                except (TraceStoreError, ReviewStoreError, OutcomeStoreError) as exc:
                    return {"given": True, "state": "unusable", "message": str(exc)}
            return {"given": True, "state": "present"}

        inputs = {
            "db": file_state(cfg.db, TraceStore.open_existing),
            "graph": file_state(cfg.graph),
            "index": file_state(cfg.index),
            "reviews": file_state(cfg.reviews, ReviewStore.open_existing),
            "outcomes": file_state(cfg.outcomes, OutcomeStore.open_existing),
            "root": file_state(cfg.root),
        }
        notes = ["read-only: this UI never writes to any store"]
        if cfg.root is None:
            notes.append(NO_ROOT_NOTE)
        return 200, {
            "data": {"version": __version__, "inputs": inputs},
            "notes": notes,
            "warnings": [],
        }

    def traces(self, params: Mapping[str, str]) -> Reply:
        with TraceStore.open_existing(_need(self.config.db, "db")) as store:
            listing = store.list()
        warnings = []
        if listing.malformed:
            warnings.append(f"{len(listing.malformed)} malformed run(s) not shown")
        return 200, {"data": listing.model_dump(mode="json"), "notes": [], "warnings": warnings}

    def trace(self, params: Mapping[str, str]) -> Reply:
        run_id = _text(params, "id")
        with TraceStore.open_existing(_need(self.config.db, "db")) as store:
            found = store.get(run_id)
        return 200, {"data": found.model_dump(mode="json"), "notes": [], "warnings": []}

    def _open_graph(self) -> tuple[Any, ExperienceGraph, Any]:
        loaded = graph_store.load(_need(self.config.graph, "graph"))
        with TraceStore.open_existing(_need(self.config.db, "db")) as store:
            report = check_sources(loaded.snapshot, store)
        return loaded, ExperienceGraph(loaded.snapshot), report

    def claims(self, params: Mapping[str, str]) -> Reply:
        _, graph, report = self._open_graph()
        reviews = self._reviews()
        outcomes = self._outcomes() or []
        current = observe_current(self.config.root) if self.config.root is not None else None
        today = utc_today()
        assessed = assess_claims(
            graph,
            report,
            reviews,
            current,
            outcomes,
            today=today,
            environment=current_environment(),
        )
        view = claims_view(
            graph,
            report,
            assessed,
            orphaned_reviews(graph, reviews),
            current,
            today,
            include_unverified=_flag(params, "include_unverified"),
        )
        details = self.config.reviews is not None or self.config.root is not None
        notes = [REPETITION_NOTE if details else CLAIM_NOTE]
        if current is None:
            notes.append(NO_ROOT_NOTE)
        warnings = []
        if view.hidden:
            warnings.append(
                f"{view.hidden} claim(s) hidden because their trace source is not confirmed"
            )
        return 200, {"data": claims_payload(view), "notes": notes, "warnings": warnings}

    def node(self, params: Mapping[str, str]) -> Reply:
        node_id = _text(params, "id")
        graph = ExperienceGraph(graph_store.load(_need(self.config.graph, "graph")).snapshot)
        with TraceStore.open_existing(_need(self.config.db, "db")) as store:
            view = scoped_node_view(
                graph,
                store,
                node_id,
                incoming=_flag(params, "incoming"),
                transitive=_flag(params, "transitive"),
                include_unverified=_flag(params, "include_unverified"),
            )
        warnings = []
        if view.hidden:
            warnings.append(
                f"{view.hidden} element(s) hidden because their trace source is not confirmed"
            )
        return 200, {"data": show_payload(view), "notes": [], "warnings": warnings}

    def check(self, params: Mapping[str, str]) -> Reply:
        loaded, _, report = self._open_graph()
        warnings = [] if report.all_ok else ["some trace sources are not confirmed; see statuses"]
        return 200, {"data": check_payload(loaded, report), "notes": [], "warnings": warnings}

    def recall(self, params: Mapping[str, str]) -> Reply:
        from cognitive_mesh.memory import MemoryIndex  # chromadb is slow to import
        from cognitive_mesh.retrieval import recall

        query = _text(params, "q")
        k = _number(params, "k", 5, int)
        min_similarity = _number(params, "min_similarity", self.config.min_similarity, float)
        loaded = graph_store.load(_need(self.config.graph, "graph"))
        reviews = self._reviews()
        outcomes = self._outcomes()
        current = observe_current(self.config.root) if self.config.root is not None else None
        today = utc_today()
        db = _need(self.config.db, "db")
        index_dir = _need(self.config.index, "index")
        with self._heavy, TraceStore.open_existing(db) as store:
            index = MemoryIndex.open_existing(index_dir, self.embedder.get())
            found = recall(
                query,
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
        notes = [f"date used for expiry: {today.isoformat()} (UTC)"]
        notes.append(
            NO_ROOT_NOTE
            if current is None
            else f"checked against the application at code version {current.fingerprint[:12]}"
        )
        return 200, {"data": found.model_dump(mode="json"), "notes": notes, "warnings": []}

    def search(self, params: Mapping[str, str]) -> Reply:
        from cognitive_mesh.memory import MemoryIndex

        query = _text(params, "q")
        k = _number(params, "k", 5, int)
        min_similarity = _number(params, "min_similarity", self.config.min_similarity, float)
        db = _need(self.config.db, "db")
        index_dir = _need(self.config.index, "index")
        with self._heavy, TraceStore.open_existing(db) as store:
            index = MemoryIndex.open_existing(index_dir, self.embedder.get())
            found = index.search(query, store, k=k, min_similarity=min_similarity)
        return 200, {"data": found.model_dump(mode="json"), "notes": [], "warnings": []}

    def graph(self, params: Mapping[str, str]) -> Reply:
        typed = params.get("path", "")
        if not typed.strip() and self.config.root is not None:
            typed = str(self.config.root)
        folder = resolve_folder(typed)
        with self._graph_lock:
            built = build_dependency_graph(ingest_application(folder))
        return 200, {
            "data": graph_payload(built, folder, full=_flag(params, "full")),
            "notes": [],
            "warnings": [],
        }

    def history(self, params: Mapping[str, str]) -> Reply:
        text = _text(params, "id")
        graph = self._experience_graph()
        node = find_node(graph, text)
        if node is None:
            return 200, {"data": empty_history(text), "notes": [], "warnings": []}
        with TraceStore.open_existing(_need(self.config.db, "db")) as store:
            assessment = None
            if node.kind is NodeKind.CLAIM:
                report = SourceReport(
                    statuses=check_attempts(graph, store, attempts_behind(graph, [node])),
                    claim_support={},
                    diagnostics=(),
                )
                current = (
                    observe_current(self.config.root) if self.config.root is not None else None
                )
                assessment = assess_claims(
                    graph,
                    report,
                    self._reviews(),
                    current,
                    self._outcomes() or [],
                    today=utc_today(),
                    environment=current_environment(),
                )[node.id]
            data = build_history(graph, store, node, assessment)
        warnings = []
        if any(r["source_check"] != "ok" for r in data["rows"]):
            warnings.append(
                "some rows rest on a trace source that is not confirmed; they are marked"
            )
        return 200, {"data": data, "notes": [data["note"]], "warnings": warnings}

    def _experience_graph(self) -> ExperienceGraph:
        return ExperienceGraph(graph_store.load(_need(self.config.graph, "graph")).snapshot)

    def graph_html(self, params: Mapping[str, str]) -> str:
        root = self.config.root
        if root is None:
            raise ApiError(409, "this view needs --root")
        view = build_view(
            build_dependency_graph(ingest_application(root)),
            DEFAULT_MAX_NODES["html"],
            DEFAULT_MAX_EDGES["html"],
        )
        return render_html(view)

    def routes(self) -> dict[str, Any]:
        return {
            "/api/status": self.status,
            "/api/traces": self.traces,
            "/api/trace": self.trace,
            "/api/claims": self.claims,
            "/api/node": self.node,
            "/api/check": self.check,
            "/api/recall": self.recall,
            "/api/search": self.search,
            "/api/graph": self.graph,
            "/api/history": self.history,
        }
