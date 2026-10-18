"""Deterministic role: answers dependency questions from the evidence-backed graph. No model."""

from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.agents.base import Agent, AgentFault, Capability, Handled, require_graph
from cognitive_mesh.agents.context import NO_PRIOR_CONTEXT
from cognitive_mesh.agents.models import AgentContext, EvidenceRef, FailureKind
from cognitive_mesh.graph import DependencyGraph, Edge, GraphError
from cognitive_mesh.graph.rederive import Holding, Rederivation, rederive

SAME_ANALYZER_NOTE = (
    "re-derived from the current graph by the same analyzer that made the claim: a repeatable "
    "check of one code version, not independent proof"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def resolve_node_id(graph: DependencyGraph, raw: str) -> str:
    """A node id, or a file path that names a module node (`src/a.py` is `module:src/a.py`).

    Only an id that exists in the graph is returned, so a path that names nothing still fails with
    the same fixed message as an unknown id.
    """
    for candidate in (raw, f"module:{raw}"):
        try:
            graph.node(candidate)
        except GraphError:
            continue
        return candidate
    raise AgentFault(FailureKind.INVALID_INPUT, "unknown node id") from None


class DependenciesOfInput(_Strict):
    node_id: str = Field(min_length=1, max_length=500)
    transitive: bool = False
    limit: int = Field(default=200, ge=1, le=1000)


class DependencyItem(_Strict):
    node_id: str
    node_kind: str
    edge_kind: str  # kind of the last edge on the way to this node
    depth: int
    evidence: list[EvidenceRef]


class DependenciesOfOutput(_Strict):
    node_id: str
    transitive: bool
    total: int
    truncated: bool
    dependencies: list[DependencyItem]


class UnresolvedReportInput(_Strict):
    limit: int = Field(default=200, ge=1, le=1000)


class UnresolvedItem(_Strict):
    node_id: str
    candidate_package: str | None  # a hint only, never a link (ADR-0006)


class UnresolvedReportOutput(_Strict):
    total: int
    truncated: bool
    unresolved: list[UnresolvedItem]
    diagnostics_by_code: dict[str, int]


class CompareWithRecalledInput(_Strict):
    limit: int = Field(default=200, ge=1, le=1000)


class CompareItem(_Strict):
    claim_id: str
    recalled_status: str  # the status the claim had when it was recalled
    now: str  # holds | refuted | subject_absent, in the current graph
    evidence: list[EvidenceRef]


class CompareWithRecalledOutput(_Strict):
    note: str
    total: int
    truncated: bool
    items: list[CompareItem]


def _evidence(edge: Edge) -> list[EvidenceRef]:
    return [
        EvidenceRef(path=e.ref.path, sha256=e.ref.sha256, line=e.line, rule=e.rule)
        for e in edge.evidence
    ]


def _unique(items: list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    return tuple(dict.fromkeys(items))


class DependencyAnalyst(Agent):
    id = "dependency_analyst"
    role = "Dependency analyst"
    description = "Answers dependency questions from the evidence-backed graph; no model calls."

    def __init__(self) -> None:
        super().__init__(
            [
                Capability(
                    name="dependencies_of",
                    description="List what a graph node depends on, with the evidence per edge.",
                    input_model=DependenciesOfInput,
                    output_model=DependenciesOfOutput,
                    handler=self._dependencies_of,
                ),
                Capability(
                    name="compare_with_recalled",
                    description="Re-check each recalled claim against the current graph; no model.",
                    input_model=CompareWithRecalledInput,
                    output_model=CompareWithRecalledOutput,
                    handler=self._compare_with_recalled,
                ),
                Capability(
                    name="unresolved_report",
                    description="List unresolved references and count graph diagnostics by code.",
                    input_model=UnresolvedReportInput,
                    output_model=UnresolvedReportOutput,
                    handler=self._unresolved_report,
                ),
            ]
        )

    @staticmethod
    def _dependencies_of(args: DependenciesOfInput, context: AgentContext) -> Handled:
        graph = require_graph(context)
        node_id = resolve_node_id(graph, args.node_id)
        try:
            if args.transitive:
                found = [
                    (r.node.id, r.node.kind.value, r.path[-1], r.depth)
                    for r in graph.transitive_dependencies(node_id)
                ]
            else:
                found = [
                    (e.target, graph.node(e.target).kind.value, e, 1)
                    for e in graph.direct_dependencies(node_id)
                ]
        except GraphError:
            raise AgentFault(FailureKind.INVALID_INPUT, "unknown node id") from None
        items = [
            DependencyItem(
                node_id=node_id,
                node_kind=node_kind,
                edge_kind=edge.kind.value,
                depth=depth,
                evidence=_evidence(edge),
            )
            for node_id, node_kind, edge, depth in found[: args.limit]
        ]
        output = DependenciesOfOutput(
            node_id=node_id,
            transitive=args.transitive,
            total=len(found),
            truncated=len(found) > args.limit,
            dependencies=items,
        )
        return Handled(output, _unique([ref for item in items for ref in item.evidence]))

    @staticmethod
    def _compare_with_recalled(args: CompareWithRecalledInput, context: AgentContext) -> Handled:
        pack = context.recalled
        if pack is None or not pack.claims:
            note = pack.message if pack is not None and pack.message else NO_PRIOR_CONTEXT
            return Handled(CompareWithRecalledOutput(note=note, total=0, truncated=False, items=[]))
        graph = require_graph(context)
        items: list[CompareItem] = []
        for claim in pack.claims[: args.limit]:
            if claim.predicate is None or claim.subject is None:
                found = Rederivation(holding=Holding.SUBJECT_ABSENT)
            else:
                found = rederive(graph, claim.predicate, claim.subject, claim.object, claim.detail)
            items.append(
                CompareItem(
                    claim_id=claim.claim_id,
                    recalled_status=claim.status,
                    now=found.holding.value,
                    evidence=[
                        EvidenceRef(path=e.path, sha256=e.sha256, line=e.line, rule=e.rule)
                        for e in found.evidence
                    ],
                )
            )
        output = CompareWithRecalledOutput(
            note=SAME_ANALYZER_NOTE,
            total=len(pack.claims),
            truncated=len(pack.claims) > args.limit,
            items=items,
        )
        return Handled(output, _unique([ref for item in items for ref in item.evidence]))

    @staticmethod
    def _unresolved_report(args: UnresolvedReportInput, context: AgentContext) -> Handled:
        graph = require_graph(context)
        nodes = graph.unresolved()
        shown = nodes[: args.limit]
        counts: dict[str, int] = {}
        for diag in graph.diagnostics:
            counts[diag.code.value] = counts.get(diag.code.value, 0) + 1
        output = UnresolvedReportOutput(
            total=len(nodes),
            truncated=len(nodes) > args.limit,
            unresolved=[
                UnresolvedItem(node_id=n.id, candidate_package=n.candidate_package) for n in shown
            ],
            diagnostics_by_code=dict(sorted(counts.items())),
        )
        evidence = [
            EvidenceRef(path=n.ref.path, sha256=n.ref.sha256, line=None, rule="unresolved_node")
            for n in shown
            if n.ref is not None
        ]
        return Handled(output, _unique(evidence))
