"""Settings and shared state for the local web UI (ADR-0031)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.memory import Embedder  # the protocol only; chromadb is not imported


class UiConfig(BaseModel):
    """Which files the UI may read. Every path is optional; a view that needs one says so."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    db: Path | None = None
    graph: Path | None = None
    index: Path | None = None
    reviews: Path | None = None
    outcomes: Path | None = None
    root: Path | None = None
    port: int = 8765
    min_similarity: float = 0.2
    embedder_factory: Callable[[], Embedder] | None = None


class EmbedderHolder:
    """One embedder per process, built on first use under a lock (the model load takes seconds)."""

    def __init__(self, factory: Callable[[], Embedder] | None) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._embedder: Embedder | None = None

    def get(self) -> Embedder:
        with self._lock:
            if self._embedder is None:
                if self._factory is None:
                    raise RuntimeError("no embedder factory configured")
                self._embedder = self._factory()
            return self._embedder
