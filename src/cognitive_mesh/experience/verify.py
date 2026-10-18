"""Re-check the graph's sources against the trace database (ADR-0015).

Statuses are computed on every call and never stored as truth. Attempts are checked with the same
function the vector index uses (`memory.documents.verify_source`); everything else is derived from
the attempts it rests on. A claim is `ok` while at least one attempt that stated it still matches.
"""

from collections.abc import Collection, Iterable

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import (
    DiagnosticCode,
    EdgeKind,
    ExperienceDiagnostic,
    ExpNode,
    NodeKind,
    Snapshot,
    SourceStatus,
)
from cognitive_mesh.memory.documents import verify_source
from cognitive_mesh.memory.models import MemoryDocument
from cognitive_mesh.traces import TraceNotFoundError, TraceRecordError, TraceStore

_PRIORITY = (SourceStatus.CHANGED, SourceStatus.UNREADABLE, SourceStatus.MISSING)
_CODES = {
    SourceStatus.MISSING: DiagnosticCode.SOURCE_MISSING,
    SourceStatus.CHANGED: DiagnosticCode.SOURCE_CHANGED,
    SourceStatus.UNREADABLE: DiagnosticCode.SOURCE_UNREADABLE,
}
_TEXT = {
    SourceStatus.MISSING: "the trace row this element rests on is gone",
    SourceStatus.CHANGED: "the trace row now yields different content than the graph recorded",
    SourceStatus.UNREADABLE: "the trace row this element rests on is malformed",
}


class SourceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statuses: dict[str, SourceStatus]
    claim_support: dict[str, int]  # claim id -> number of attempts that still match
    diagnostics: tuple[ExperienceDiagnostic, ...]

    @property
    def all_ok(self) -> bool:
        return all(s is SourceStatus.OK for s in self.statuses.values())

    def is_supported(self, node_id: str) -> bool:
        return self.statuses.get(node_id) is SourceStatus.OK


def _aggregate(statuses: list[SourceStatus]) -> SourceStatus:
    if SourceStatus.OK in statuses:
        return SourceStatus.OK
    for candidate in _PRIORITY:
        if candidate in statuses:
            return candidate
    return SourceStatus.UNREADABLE  # nothing to rest on


def _check_attempts(
    attempts: Iterable[ExpNode],
    store: TraceStore,
    cache: dict[str, dict[str, MemoryDocument] | None],
) -> dict[str, SourceStatus]:
    statuses: dict[str, SourceStatus] = {}
    for node in attempts:
        ref = node.ref
        if ref is None or ref.step_id is None or ref.attempt_no is None or ref.text_sha256 is None:
            statuses[node.id] = SourceStatus.UNREADABLE
            continue
        _, check = verify_source(
            store, cache, ref.run_id, ref.step_id, ref.attempt_no, ref.text_sha256
        )
        statuses[node.id] = SourceStatus(check.value)
    return statuses


def check_attempts(
    graph: ExperienceGraph, store: TraceStore, attempt_ids: Collection[str]
) -> dict[str, SourceStatus]:
    """Source status of just these attempts (ADR-0019): same check as `check_sources`, scoped.

    Ids that are not attempt nodes of the graph are ignored.
    """
    wanted = [graph.node(i) for i in sorted(attempt_ids) if graph.has_node(i)]
    return _check_attempts((n for n in wanted if n.kind is NodeKind.ATTEMPT), store, {})


def check_sources(snapshot: Snapshot, store: TraceStore) -> SourceReport:
    graph = ExperienceGraph(snapshot)
    cache: dict[str, dict[str, MemoryDocument] | None] = {}
    statuses = _check_attempts(graph.nodes(NodeKind.ATTEMPT), store, cache)

    for node in graph.nodes(NodeKind.RUN):
        statuses[node.id] = _run_status(store, cache, node)

    for node in graph.nodes(NodeKind.STEP):
        final = [e.target for e in graph.out_edges(node.id, {EdgeKind.FINAL_ATTEMPT})]
        statuses[node.id] = _aggregate([statuses[a] for a in final if a in statuses])

    support: dict[str, int] = {}
    for node in graph.claims():
        evidence = [e.target for e in graph.evidence(node.id)]
        found = [statuses[a] for a in evidence if a in statuses]
        statuses[node.id] = _aggregate(found)
        support[node.id] = sum(1 for s in found if s is SourceStatus.OK)

    for node in graph.nodes():
        if node.id in statuses:
            continue  # entities: capability, agent, target
        around = [
            statuses[e.source]
            for e in graph.in_edges(node.id)
            if e.source in statuses
            and graph.node(e.source).kind in (NodeKind.ATTEMPT, NodeKind.CLAIM)
        ]
        statuses[node.id] = _aggregate(around)

    diagnostics = tuple(
        ExperienceDiagnostic(
            code=_CODES[statuses[n.id]],
            run_id=n.ref.run_id if n.ref else None,
            node_id=n.id,
            message=_TEXT[statuses[n.id]],
        )
        for n in graph.nodes()
        if n.kind in (NodeKind.RUN, NodeKind.ATTEMPT) and statuses[n.id] is not SourceStatus.OK
    )
    return SourceReport(statuses=statuses, claim_support=support, diagnostics=diagnostics)


def _run_status(
    store: TraceStore, cache: dict[str, dict[str, MemoryDocument] | None], node: ExpNode
) -> SourceStatus:
    if node.ref is None:
        return SourceStatus.UNREADABLE
    run_id = node.ref.run_id
    if run_id not in cache:  # no attempt of this run was checked
        try:
            store.get(run_id)
        except TraceNotFoundError:
            return SourceStatus.MISSING
        except TraceRecordError:
            return SourceStatus.UNREADABLE
        return SourceStatus.OK
    docs = cache[run_id]
    if docs is None:
        return SourceStatus.UNREADABLE
    if docs:
        return SourceStatus.OK
    try:  # the run no longer yields any document: gone, or no longer finished
        store.get(run_id)
    except TraceNotFoundError:
        return SourceStatus.MISSING
    except TraceRecordError:
        return SourceStatus.UNREADABLE
    return SourceStatus.CHANGED
