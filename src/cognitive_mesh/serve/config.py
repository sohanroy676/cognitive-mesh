"""Settings and shared state for the three local servers (ADR-0044).

Everything the servers read or write lives under one `home` folder, laid out the way
`evaluate.memory.ArmPaths` lays out an arm, so the same memory code serves chat and evaluation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cognitive_mesh.change.checks import CheckResult, run_check
from cognitive_mesh.evaluate.memory import ArmPaths
from cognitive_mesh.memory import Embedder

if TYPE_CHECKING:
    from cognitive_mesh.providers import ModelClient

CheckRunner = Callable[[tuple[str, ...], Path, int], CheckResult]
ClientFactory = Callable[[str], "ModelClient"]  # catalog entry id -> a client for it

DEFAULT_APP_PORT = 8770
DEFAULT_GRAPH_PORT = 8771
DEFAULT_DATA_PORT = 8772


@dataclass(frozen=True)
class ServeConfig:
    home: Path
    catalog: Path | None = None
    conductor: str | None = None
    app_port: int = DEFAULT_APP_PORT
    graph_port: int = DEFAULT_GRAPH_PORT
    data_port: int = DEFAULT_DATA_PORT
    allow_unisolated_checks: bool = False
    embedder_factory: Callable[[], Embedder] | None = None
    # Tests give fakes; the real command leaves them None.
    client_factory: ClientFactory | None = None
    check_runner: CheckRunner = field(default=run_check)

    @property
    def paths(self) -> ArmPaths:
        return ArmPaths(self.home)

    @property
    def workspace(self) -> Path:
        return self.home / "workspace.sqlite3"

    @property
    def chat(self) -> Path:
        return self.home / "chat.sqlite3"

    @property
    def trace(self) -> Path:
        return self.paths.trace

    def sources(self) -> dict[str, Path]:
        """The SQLite files the data server may show, by short name (only those that exist)."""
        named = {
            "trace": self.trace,
            "workspace": self.workspace,
            "chat": self.chat,
            "outcomes": self.paths.outcomes,
            "graph": self.paths.graph,
        }
        return {name: path for name, path in named.items() if path.is_file()}


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
