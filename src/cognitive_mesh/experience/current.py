"""Re-derive claims from the application as it is now (ADR-0016).

This is a check against one code version: the directory is ingested, its fingerprint computed (the
same function the trace recorder uses) and the deterministic dependency graph rebuilt. The result is
produced by the same analyzer that made the claim, so it is a repeatable derivation, not independent
proof, and it says nothing about code that was not ingested.
"""

from pathlib import Path

from cognitive_mesh.graph import DependencyGraph, build_dependency_graph
from cognitive_mesh.graph.rederive import CurrentEvidence, Holding, Rederivation, rederive
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.traces import source_fingerprint

__all__ = [
    "CurrentEvidence",
    "CurrentSource",
    "Holding",
    "Rederivation",
    "observe_current",
]


class CurrentSource:
    """The application at one moment: its fingerprint and its rebuilt dependency graph."""

    def __init__(self, fingerprint: str, graph: DependencyGraph) -> None:
        self.fingerprint = fingerprint
        self._graph = graph

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        """Look one claim up. An unknown predicate is `subject_absent`, never a validation."""
        return rederive(self._graph, predicate, subject, obj, detail)


def observe_current(root: Path) -> CurrentSource:
    """Ingest `root`. Raises `IngestionError` only when the root itself is unusable."""
    ingestion = ingest_application(root)
    return CurrentSource(source_fingerprint(ingestion), build_dependency_graph(ingestion))
