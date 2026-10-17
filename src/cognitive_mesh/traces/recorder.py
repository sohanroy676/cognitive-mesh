"""Turns an orchestrator run into trace records: redaction, ids, source, environment (ADR-0011)."""

import hashlib
import platform
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import JsonValue, TypeAdapter

from cognitive_mesh import __version__
from cognitive_mesh.agents import AgentResult
from cognitive_mesh.ingestion import IngestionResult
from cognitive_mesh.ingestion.secrets import redact_secrets
from cognitive_mesh.orchestrator import StepRecord, TaskRun, ValidatedPlan
from cognitive_mesh.traces.models import (
    AttemptTrace,
    RunTrace,
    StepTrace,
    TraceEnvironment,
    TraceStatus,
    trace_status,
)
from cognitive_mesh.traces.store import TraceStore

_RESULT = TypeAdapter(AgentResult)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _new_run_id() -> str:
    return uuid.uuid4().hex


def redact_json(value: JsonValue, rules: set[str]) -> JsonValue:
    """Redact every string in a JSON value; the names of the rules that fired go into `rules`."""
    if isinstance(value, str):
        text, found = redact_secrets(value)
        rules.update(r.rule for r in found)
        return text
    if isinstance(value, list):
        return [redact_json(v, rules) for v in value]
    if isinstance(value, dict):
        return {k: redact_json(v, rules) for k, v in value.items()}
    return value


def source_fingerprint(ingestion: IngestionResult) -> str:
    """sha256 over the sorted (path, sha256) pairs of the ingested files."""
    digest = hashlib.sha256()
    for path, sha in sorted((f.ref.path, f.ref.sha256) for f in ingestion.files):
        digest.update(f"{path}\0{sha}\n".encode())
    return digest.hexdigest()


class TraceRecorder:
    """`begin` commits a `running` header before the run; `finish` writes everything at once."""

    def __init__(
        self,
        store: TraceStore,
        *,
        now: Callable[[], datetime] = utc_now,
        new_run_id: Callable[[], str] = _new_run_id,
    ) -> None:
        self._store = store
        self._now = now
        self._new_run_id = new_run_id
        self._rules: dict[str, set[str]] = {}

    def begin(self, plan: ValidatedPlan, ingestion: IngestionResult | None = None) -> str:
        rules: set[str] = set()
        raw: dict[str, JsonValue] = {
            "task_id": plan.task_id,
            "steps": [s.model_dump(mode="json") for s in plan.order],
        }
        plan_json = {key: redact_json(value, rules) for key, value in raw.items()}
        run_id = self._new_run_id()
        self._store.begin_run(
            RunTrace(
                run_id=run_id,
                task_id=plan.task_id,
                status=TraceStatus.INTERRUPTED,
                started_at=self._now(),
                source_root=ingestion.root if ingestion else None,
                source_fingerprint=source_fingerprint(ingestion) if ingestion else None,
                environment=TraceEnvironment(
                    cognitive_mesh_version=__version__,
                    python_version=platform.python_version(),
                    platform=platform.system(),
                ),
                redactions=tuple(sorted(rules)),
                plan=plan_json,
            )
        )
        self._rules[run_id] = rules
        return run_id

    def finish(self, run_id: str, run: TaskRun) -> TraceStatus:
        rules = self._rules.pop(run_id, set())
        steps = tuple(
            _step_trace(seq, step, run.task_id, rules) for seq, step in enumerate(run.steps)
        )
        status = trace_status(run.status)
        self._store.finish_run(run_id, status, self._now(), sorted(rules), steps)
        return status


def _step_trace(seq: int, step: StepRecord, task_id: str, rules: set[str]) -> StepTrace:
    attempts = []
    for n, result in enumerate(step.attempts, start=1):
        redacted = redact_json(result.model_dump(mode="json"), rules)
        clean = _RESULT.validate_python(redacted)
        attempts.append(
            AttemptTrace(
                attempt_no=n,
                agent_task_id=f"{task_id}.{step.step_id}.{n}",
                agent_id=clean.agent_id,
                result=clean,
            )
        )
    message = None
    if step.message is not None:
        message, found = redact_secrets(step.message)
        rules.update(r.rule for r in found)
    return StepTrace(
        seq=seq,
        step_id=step.step_id,
        capability=step.capability,
        status=step.status,
        agent_id=step.agent_id,
        attempt_count=step.attempt_count,
        failure_kind=step.failure_kind,
        message=message,
        blocked_by=step.blocked_by,
        started_at=step.started_at,
        finished_at=step.finished_at,
        attempts=tuple(attempts),
    )
