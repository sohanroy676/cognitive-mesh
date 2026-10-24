"""Every recorded step, as the Experiences tab lists it (ADR-0033).

An experience is the final attempt of one step of a finished run (ADR-0014). The list is read from
the trace database, so it is always what is recorded now. The experience graph, when given, only
adds the claims that point at each step. No row carries a step's output or text.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Final

from cognitive_mesh.experience.models import EdgeKind, NodeKind
from cognitive_mesh.memory.documents import eligible_documents

if TYPE_CHECKING:
    from cognitive_mesh.experience.graph import ExperienceGraph
    from cognitive_mesh.traces import TraceStore

MAX_ROWS: Final = 10000


def build_experiences(
    store: TraceStore, graph: ExperienceGraph | None, built_at: str | None
) -> dict[str, Any]:
    """Rows newest first (up to `MAX_ROWS`), counts over all of them, and how the graph compares."""
    rows: list[dict[str, Any]] = []
    total = 0
    by_capability: Counter[str] = Counter()
    by_result: Counter[str] = Counter()
    by_basis: Counter[str] = Counter()
    malformed: list[str] = []
    without = 0
    runs_with = 0
    in_db: set[str] = set()
    in_graph = {n.id for n in graph.nodes(NodeKind.ATTEMPT)} if graph else set()
    not_in_graph = 0
    for run_id, trace in store.iter_runs():
        if trace is None:
            malformed.append(run_id)
            continue
        docs = eligible_documents(trace)
        if not docs:
            without += 1
            continue
        runs_with += 1
        for doc in docs:
            total += 1
            node_id = f"attempt:{doc.doc_id}"
            in_db.add(node_id)
            by_capability[doc.capability] += 1
            by_result[doc.result_status] += 1
            by_basis[doc.basis or "none"] += 1
            if graph is not None and node_id not in in_graph:
                not_in_graph += 1
            if len(rows) >= MAX_ROWS:
                continue
            rows.append(
                {
                    "id": doc.doc_id,
                    "run_id": trace.run_id,
                    "started_at": trace.started_at.isoformat(),
                    "run_status": trace.status.value,
                    "step_id": doc.step_id,
                    "attempt_no": doc.attempt_no,
                    "capability": doc.capability,
                    "result": doc.result_status,
                    "basis": doc.basis,
                    "code_version": trace.source_fingerprint,
                    "claims": _claims(graph, node_id) if graph is not None else None,
                }
            )
    return {
        "rows": rows,
        "total": total,
        "shown": len(rows),
        "truncated": total > len(rows),
        "limit": MAX_ROWS,
        "counts": {
            "capability": dict(sorted(by_capability.items())),
            "result": dict(sorted(by_result.items())),
            "basis": dict(sorted(by_basis.items())),
        },
        "runs": runs_with,
        "runs_without_experiences": without,
        "malformed": malformed,
        "graph": {
            "given": graph is not None,
            "built_at": built_at,
            "not_in_graph": not_in_graph if graph is not None else None,
            "missing_from_database": len(in_graph - in_db) if graph is not None else None,
        },
    }


def _claims(graph: ExperienceGraph, attempt_id: str) -> list[dict[str, str]]:
    if not graph.has_node(attempt_id):
        return []
    return [
        {"id": e.source, "name": graph.node(e.source).name}
        for e in graph.in_edges(attempt_id, {EdgeKind.EVIDENCED_BY})
    ]
