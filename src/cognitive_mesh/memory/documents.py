"""Which trace content is eligible for embedding, and the exact text that is embedded (ADR-0014).

Pure functions over a `RunTrace`; nothing here touches a database or a model.
"""

import hashlib
import json
from typing import Final

from pydantic import JsonValue

from cognitive_mesh.agents import AgentSuccess
from cognitive_mesh.memory.models import MemoryDocument
from cognitive_mesh.traces import RunTrace, StepTrace, TraceStatus

TEXT_FORMAT_VERSION: Final = 1
MAX_TEXT_CHARS: Final = 8000  # part of the format: longer text is cut, deterministically

_FINISHED = frozenset({TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.INCOMPLETE})


def _dump(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _plan_step(trace: RunTrace, step_id: str) -> dict[str, JsonValue]:
    steps = trace.plan.get("steps")
    if isinstance(steps, list):
        for item in steps:
            if isinstance(item, dict) and item.get("step_id") == step_id:
                return item
    return {}


def _step_document(trace: RunTrace, step: StepTrace) -> MemoryDocument | None:
    if not step.attempts:  # blocked, not_run, or only invalid results: nothing to remember
        return None
    final = step.attempts[-1]
    result = final.result
    planned = _plan_step(trace, step.step_id)
    lines = [
        f"capability: {step.capability}",
        f"inputs: {_dump(planned.get('inputs', {}))}",
    ]
    basis: str | None
    if isinstance(result, AgentSuccess):
        basis = result.basis.value
        lines += ["result: success", f"output: {_dump(result.output)}"]
    else:
        basis = None
        lines += [
            "result: failure",
            f"failure_kind: {result.kind.value}",
            f"message: {result.message}",
        ]
    text = "\n".join(lines)[:MAX_TEXT_CHARS]
    return MemoryDocument(
        run_id=trace.run_id,
        step_id=step.step_id,
        attempt_no=final.attempt_no,
        capability=step.capability,
        result_status=result.status,
        basis=basis,
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def eligible_documents(trace: RunTrace) -> tuple[MemoryDocument, ...]:
    """Documents for a finished run; an interrupted run has none."""
    if trace.status not in _FINISHED:
        return ()
    docs = (_step_document(trace, step) for step in trace.steps)
    return tuple(d for d in docs if d is not None)
