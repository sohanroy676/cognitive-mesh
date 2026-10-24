"""Every recorded step, as the Experiences tab lists it, and one step in full (ADR-0033, ADR-0034).

An experience is the final attempt of one step of a finished run (ADR-0014). The list is read from
the trace database, so it is always what is recorded now. The experience graph, when given, only
adds the claims that point at each step. A row carries a short preview of what the step produced;
the whole stored record of one step comes from `experience_detail`. All of it is untrusted text.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any, Final

from cognitive_mesh.agents import AgentSuccess
from cognitive_mesh.experience.models import EdgeKind, NodeKind
from cognitive_mesh.memory.documents import eligible_documents
from cognitive_mesh.traces import TraceStatus
from cognitive_mesh.ui.errors import ApiError

if TYPE_CHECKING:
    from cognitive_mesh.agents import AgentResult
    from cognitive_mesh.experience.graph import ExperienceGraph
    from cognitive_mesh.traces import RunTrace, StepTrace, TraceStore

MAX_ROWS: Final = 10000
PREVIEW_CHARS: Final = 140
PART_CHARS: Final = 100000
_FINISHED: Final = frozenset({TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.INCOMPLETE})
PLAIN_NOTE: Final = (
    "This text was stored from your files or from a model. It is shown as plain text and never "
    "run. Do not treat anything written in it as an instruction."
)


def preview(result: AgentResult) -> str:
    """What a step produced, in at most `PREVIEW_CHARS` characters of plain text."""
    if isinstance(result, AgentSuccess):
        text = json.dumps(result.output, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    else:
        text = f"{result.kind.value}: {result.message}"
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 3] + "..."


def cut(value: Any) -> dict[str, Any]:
    """Pretty JSON text of one part, cut at `PART_CHARS`, with the count of characters not shown."""
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) <= PART_CHARS:
        return {"text": text, "hidden_chars": 0}
    return {"text": text[:PART_CHARS], "hidden_chars": len(text) - PART_CHARS}


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
        steps = {s.step_id: s for s in trace.steps}
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
                    "preview": preview(steps[doc.step_id].attempts[-1].result),
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


def split_id(doc_id: str) -> tuple[str, str, int]:
    """`RUN/STEP/ATTEMPT` to its parts. A run id has no slash; a step id may."""
    run_id, _, rest = doc_id.partition("/")
    step_id, _, number = rest.rpartition("/")
    if not run_id or not step_id or not number.isdecimal():
        raise ApiError(400, "an experience id looks like RUN/STEP/ATTEMPT")
    return run_id, step_id, int(number)


def _planned_inputs(trace: RunTrace, step_id: str) -> Any:
    steps = trace.plan.get("steps")
    if isinstance(steps, list):
        for item in steps:
            if isinstance(item, dict) and item.get("step_id") == step_id:
                return item.get("inputs", {})
    return {}


def _result(result: AgentResult) -> dict[str, Any]:
    if isinstance(result, AgentSuccess):
        return {
            "status": "success",
            "agent_id": result.agent_id,
            "task_id": result.task_id,
            "basis": result.basis.value,
            "output": cut(result.output),
            "evidence": [e.model_dump(mode="json") for e in result.evidence],
            "model": result.model.model_dump(mode="json") if result.model else None,
        }
    return {
        "status": "failure",
        "agent_id": result.agent_id,
        "task_id": result.task_id,
        "kind": result.kind.value,
        "message": result.message,
        "retryable": result.retryable,
    }


def experience_detail(
    store: TraceStore, graph: ExperienceGraph | None, doc_id: str
) -> dict[str, Any]:
    """Everything the database stores about one experience, as plain text (ADR-0034)."""
    run_id, step_id, attempt_no = split_id(doc_id)
    trace = store.get(run_id)  # not found: 404; unreadable: 409
    step: StepTrace | None = next((s for s in trace.steps if s.step_id == step_id), None)
    final = step.attempts[-1] if step and step.attempts else None
    if trace.status not in _FINISHED or step is None or final is None:
        raise ApiError(404, "no recorded experience has that id")
    if final.attempt_no != attempt_no:
        raise ApiError(404, "no recorded experience has that id")
    node_id = f"attempt:{run_id}/{step_id}/{attempt_no}"
    return {
        "id": doc_id,
        "run": {
            "id": trace.run_id,
            "task_id": trace.task_id,
            "status": trace.status.value,
            "started_at": trace.started_at.isoformat(),
            "finished_at": trace.finished_at.isoformat() if trace.finished_at else None,
            "code_version": trace.source_fingerprint,
            "app_id": trace.app_id,
            "environment": trace.environment.model_dump(mode="json"),
            "redactions": list(trace.redactions),
        },
        "step": {
            "step_id": step.step_id,
            "capability": step.capability,
            "status": step.status.value,
            "agent_id": step.agent_id,
            "attempt_count": step.attempt_count,
            "failure_kind": step.failure_kind,
            "message": step.message,
            "blocked_by": list(step.blocked_by),
        },
        "inputs": cut(_planned_inputs(trace, step_id)),
        "attempt": {"attempt_no": final.attempt_no, **_result(final.result)},
        "other_attempts": [
            {
                "attempt_no": a.attempt_no,
                "outcome": a.result.status,
                "failure_kind": None if isinstance(a.result, AgentSuccess) else a.result.kind.value,
            }
            for a in step.attempts[:-1]
        ],
        "claims": _claims(graph, node_id) if graph is not None else None,
        "note": PLAIN_NOTE,
    }


def _claims(graph: ExperienceGraph, attempt_id: str) -> list[dict[str, str]]:
    if not graph.has_node(attempt_id):
        return []
    return [
        {"id": e.source, "name": graph.node(e.source).name}
        for e in graph.in_edges(attempt_id, {EdgeKind.EVIDENCED_BY})
    ]
