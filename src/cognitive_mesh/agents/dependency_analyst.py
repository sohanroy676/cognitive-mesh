"""Deterministic role: answers dependency questions from the evidence-backed graph. No model."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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


class CompareDependenciesInput(_Strict):
    """Two `dependencies_of` outputs, normally taken from earlier steps through declared state."""

    first: dict[str, JsonValue]
    second: dict[str, JsonValue]
    limit: int = Field(default=200, ge=1, le=1000)


class CompareDependenciesOutput(_Strict):
    first_node: str
    second_node: str
    shared: list[str]
    only_first: list[str]
    only_second: list[str]
    shared_total: int
    only_first_total: int
    only_second_total: int
    truncated: bool  # one of the lists above was cut at `limit`
    inputs_truncated: bool  # an input report was itself cut off, so the lists may be incomplete


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


def _report_ids(name: str, report: dict[str, JsonValue]) -> tuple[str, set[str], bool]:
    """(node id, dependency node ids, report was truncated) of a dependencies_of output.

    A malformed report fails with a message that names the input, never its content.
    """
    node = report.get("node_id")
    items = report.get("dependencies")
    if not isinstance(node, str) or not isinstance(items, list):
        raise AgentFault(
            FailureKind.INVALID_INPUT, f"input {name!r} is not a dependencies_of report"
        )
    ids: set[str] = set()
    for item in items:
        target = item.get("node_id") if isinstance(item, dict) else None
        if not isinstance(target, str):
            raise AgentFault(
                FailureKind.INVALID_INPUT, f"input {name!r} has a dependency without a node id"
            )
        ids.add(target)
    return node, ids, report.get("truncated") is True


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
                    name="compare_dependencies",
                    description="Compare two dependencies_of reports; no model.",
                    input_model=CompareDependenciesInput,
                    output_model=CompareDependenciesOutput,
                    handler=self._compare_dependencies,
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
    def _compare_dependencies(args: CompareDependenciesInput, context: AgentContext) -> Handled:
        first_node, first_ids, first_cut = _report_ids("first", args.first)
        second_node, second_ids, second_cut = _report_ids("second", args.second)
        lists = {
            "shared": sorted(first_ids & second_ids),
            "only_first": sorted(first_ids - second_ids),
            "only_second": sorted(second_ids - first_ids),
        }
        output = CompareDependenciesOutput(
            first_node=first_node,
            second_node=second_node,
            shared=lists["shared"][: args.limit],
            only_first=lists["only_first"][: args.limit],
            only_second=lists["only_second"][: args.limit],
            shared_total=len(lists["shared"]),
            only_first_total=len(lists["only_first"]),
            only_second_total=len(lists["only_second"]),
            truncated=any(len(v) > args.limit for v in lists.values()),
            inputs_truncated=first_cut or second_cut,
        )
        return Handled(output)

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
