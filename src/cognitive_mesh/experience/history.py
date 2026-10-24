"""Every recorded run that touched a node or a claim (ADR-0032 point 6).

Pure functions over a loaded graph and an open trace store. Rows are raw records: a run being listed
says it happened, not that anything it said is true. Only the attempts a row rests on are
source-checked (ADR-0028), and a row whose source is not confirmed is marked, never dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from cognitive_mesh.experience.models import EdgeKind, ExpNode, NodeKind
from cognitive_mesh.experience.verify import check_attempts
from cognitive_mesh.traces import TraceNotFoundError, TraceStoreError

if TYPE_CHECKING:
    from cognitive_mesh.experience.assess import ClaimAssessment
    from cognitive_mesh.experience.graph import ExperienceGraph
    from cognitive_mesh.traces import RunTrace, TraceStore

MAX_RUNS: Final = 500
RAW_NOTE: Final = "a listed run is a raw record of what happened, not validated knowledge"


def find_node(graph: ExperienceGraph, text: str) -> ExpNode | None:
    """The node for an id, or for a dependency-graph id (its `target:` node). None if absent."""
    for candidate in (text, f"target:{text}"):
        if graph.has_node(candidate):
            return graph.node(candidate)
    return None


def _unique(nodes: list[ExpNode]) -> list[ExpNode]:
    return sorted({n.id: n for n in nodes}.values(), key=lambda n: n.id)


def attempts_and_claims(
    graph: ExperienceGraph, node: ExpNode
) -> tuple[list[ExpNode], list[ExpNode]]:
    """The attempts that touched this node, and (for a target) the claims that are about it."""
    kind = node.kind
    if kind is NodeKind.ATTEMPT:
        return [node], []
    if kind is NodeKind.CLAIM:
        return _unique([graph.node(e.target) for e in graph.evidence(node.id)]), []
    if kind is NodeKind.TARGET:
        about = graph.in_edges(node.id, {EdgeKind.ABOUT})
        claims = graph.in_edges(node.id, {EdgeKind.SUBJECT, EdgeKind.OBJECT})
        return (
            _unique([graph.node(e.source) for e in about]),
            _unique([graph.node(e.source) for e in claims]),
        )
    if kind in (NodeKind.RUN, NodeKind.STEP):
        reached = graph.reachable(
            node.id,
            forward=True,
            transitive=True,
            kinds={EdgeKind.HAS_STEP, EdgeKind.FINAL_ATTEMPT},
        )
        return _unique([r.node for r in reached if r.node.kind is NodeKind.ATTEMPT]), []
    via = EdgeKind.USED_CAPABILITY if kind is NodeKind.CAPABILITY else EdgeKind.RAN_BY
    return _unique([graph.node(e.source) for e in graph.in_edges(node.id, {via})]), []


def _capability(trace: RunTrace, step_id: str) -> str | None:
    for step in trace.steps:
        if step.step_id == step_id:
            return step.capability
    return None


def build_history(
    graph: ExperienceGraph,
    store: TraceStore,
    node: ExpNode,
    assessment: ClaimAssessment | None = None,
) -> dict[str, Any]:
    """Rows for every run that touched `node`, newest first, with what each said about it."""
    attempts, claims = attempts_and_claims(graph, node)
    rows: list[dict[str, Any]] = []
    for a in attempts:
        rows.append(_row(a, "supports" if node.kind is NodeKind.CLAIM else "ran", None))
    if assessment is not None:
        for c in assessment.contradicting:
            if graph.has_node(c.attempt_id):
                rows.append(_row(graph.node(c.attempt_id), "contradicts", c.same_version))
    statuses = check_attempts(graph, store, {r["attempt_id"] for r in rows})
    traces: dict[str, RunTrace | None] = {}
    for row in rows:
        row["source_check"] = (
            statuses[row["attempt_id"]].value if row["attempt_id"] in statuses else "unchecked"
        )
        run_id = row["run_id"]
        if run_id not in traces:
            try:
                traces[run_id] = store.get(run_id)
            except (TraceNotFoundError, TraceStoreError):
                traces[run_id] = None
        trace = traces[run_id]
        row["run_status"] = trace.status.value if trace else "unreadable"
        row["started_at"] = trace.started_at.isoformat() if trace else None
        row["code_version"] = trace.source_fingerprint if trace else None
        row["task_id"] = trace.task_id if trace else None
        row["capability"] = _capability(trace, row["step_id"]) if trace else None
    rows.sort(key=lambda r: (r["run_id"], r["attempt_id"]))
    rows.sort(key=lambda r: r["started_at"] or "", reverse=True)  # newest first, unreadable last
    runs = {r["run_id"] for r in rows}
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row["run_id"] not in seen:
            if len(seen) >= MAX_RUNS:
                continue
            seen.add(row["run_id"])
        kept.append(row)
    return {
        "found": True,
        "node": {"id": node.id, "kind": node.kind.value, "name": node.name},
        "claims": [{"id": c.id, "name": c.name} for c in claims],
        "rows": kept,
        "total_runs": len(runs),
        "truncated": len(runs) > len(seen),
        "status": assessment.status.value if assessment else None,
        "note": RAW_NOTE,
    }


def empty_history(text: str) -> dict[str, Any]:
    return {
        "found": False,
        "node": {"id": text, "kind": None, "name": text},
        "claims": [],
        "rows": [],
        "total_runs": 0,
        "truncated": False,
        "status": None,
        "note": RAW_NOTE,
    }


def _row(attempt: ExpNode, role: str, same_version: bool | None) -> dict[str, Any]:
    ref = attempt.ref
    return {
        "attempt_id": attempt.id,
        "run_id": ref.run_id if ref else "",
        "step_id": ref.step_id if ref else "",
        "attempt_no": ref.attempt_no if ref else None,
        "role": role,
        "same_version": same_version,
    }
