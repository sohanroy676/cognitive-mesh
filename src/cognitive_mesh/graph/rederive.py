"""Re-check one claim against a dependency graph (ADR-0016, shared by ADR-0017).

Pure: the graph is the only input. The answer comes from the same analyzer that produced the claim,
so it is a repeatable derivation, not independent proof.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.graph.graph import DependencyGraph
from cognitive_mesh.graph.models import GraphError, NodeKind


class Holding(StrEnum):
    HOLDS = "holds"  # the graph states the claim
    REFUTED = "refuted"  # the subject exists and the graph does not state the claim
    SUBJECT_ABSENT = "subject_absent"  # the subject is not in the graph: neither way


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


def rederive(
    graph: DependencyGraph, predicate: str, subject: str, obj: str | None, detail: str | None
) -> Rederivation:
    """Look one claim up. An unknown predicate is `subject_absent`, never a validation."""
    try:
        node = graph.node(subject)
    except GraphError:
        return Rederivation(holding=Holding.SUBJECT_ABSENT)
    if predicate == "depends_on":
        for edge in graph.direct_dependencies(subject):
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
                    path=node.ref.path, sha256=node.ref.sha256, line=None, rule="unresolved_node"
                ),
            )
            if node.ref is not None
            else ()
        )
        return Rederivation(holding=Holding.HOLDS, evidence=evidence)
    return Rederivation(holding=Holding.SUBJECT_ABSENT)
