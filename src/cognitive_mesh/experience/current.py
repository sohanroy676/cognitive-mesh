"""Re-derive claims from the application as it is now (ADR-0016).

This is a check against one code version: the directory is ingested, its fingerprint computed (the
same function the trace recorder uses) and the deterministic dependency graph rebuilt. The result is
produced by the same analyzer that made the claim, so it is a repeatable derivation, not independent
proof, and it says nothing about code that was not ingested.
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.graph import DependencyGraph, GraphError, NodeKind, build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.traces import source_fingerprint


class Holding(StrEnum):
    HOLDS = "holds"  # the current graph states the claim
    REFUTED = "refuted"  # the subject exists now and the graph does not state the claim
    SUBJECT_ABSENT = "subject_absent"  # the subject is not in the current graph: neither way


class CurrentEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str
    line: int | None
    rule: str


class Rederivation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    holding: Holding
    evidence: tuple[CurrentEvidence, ...] = ()


class CurrentSource:
    """The application at one moment: its fingerprint and its rebuilt dependency graph."""

    def __init__(self, fingerprint: str, graph: DependencyGraph) -> None:
        self.fingerprint = fingerprint
        self._graph = graph

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        """Look one claim up. An unknown predicate is `subject_absent`, never a validation."""
        try:
            node = self._graph.node(subject)
        except GraphError:
            return Rederivation(holding=Holding.SUBJECT_ABSENT)
        if predicate == "depends_on":
            for edge in self._graph.direct_dependencies(subject):
                if edge.target == obj and edge.kind.value == detail:
                    return Rederivation(
                        holding=Holding.HOLDS,
                        evidence=tuple(
                            CurrentEvidence(
                                path=e.ref.path, sha256=e.ref.sha256, line=e.line, rule=e.rule
                            )
                            for e in edge.evidence
                        ),
                    )
            return Rederivation(holding=Holding.REFUTED)
        if predicate == "is_unresolved":
            if node.kind is not NodeKind.UNRESOLVED:
                return Rederivation(holding=Holding.REFUTED)
            evidence = (
                (
                    CurrentEvidence(
                        path=node.ref.path,
                        sha256=node.ref.sha256,
                        line=None,
                        rule="unresolved_node",
                    ),
                )
                if node.ref is not None
                else ()
            )
            return Rederivation(holding=Holding.HOLDS, evidence=evidence)
        return Rederivation(holding=Holding.SUBJECT_ABSENT)


def observe_current(root: Path) -> CurrentSource:
    """Ingest `root`. Raises `IngestionError` only when the root itself is unusable."""
    ingestion = ingest_application(root)
    return CurrentSource(source_fingerprint(ingestion), build_dependency_graph(ingestion))
