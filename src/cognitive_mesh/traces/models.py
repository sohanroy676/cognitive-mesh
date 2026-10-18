"""Trace records: what was stored about a run, its steps and its attempts (ADR-0011)."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from cognitive_mesh.agents import AgentResult, RecalledContext
from cognitive_mesh.identity import IdentityHow
from cognitive_mesh.orchestrator import StepRecord, StepStatus, TaskRun, TaskStatus


class TraceStoreError(Exception):
    """Storage failed or the database is unusable. Messages never contain SQL or stored values."""


class TraceRecordError(TraceStoreError):
    """A stored run is malformed. Names the run id and the kind of problem, never values."""


class TraceNotFoundError(Exception):
    """No run has this id."""


class TraceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    INTERRUPTED = "interrupted"  # started but never finished: a crash or a rolled-back write


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TraceEnvironment(_Frozen):
    cognitive_mesh_version: str
    python_version: str
    platform: str


class AttemptTrace(_Frozen):
    """One valid agent result. `agent_task_id` is `{task}.{step}.{attempt_no}`."""

    attempt_no: int
    agent_task_id: str
    agent_id: str
    result: AgentResult


class StepTrace(_Frozen):
    seq: int
    step_id: str
    capability: str
    status: StepStatus
    agent_id: str | None = None
    attempt_count: int = 0
    failure_kind: str | None = None
    message: str | None = None
    blocked_by: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: tuple[AttemptTrace, ...] = ()


class RunTrace(_Frozen):
    run_id: str
    task_id: str
    status: TraceStatus
    started_at: datetime
    finished_at: datetime | None = None
    source_root: str | None = None
    source_fingerprint: str | None = None
    # Application identity (ADR-0021). None: recorded before schema 3, or no source analysed.
    app_id: str | None = None
    app_id_how: IdentityHow | None = None
    environment: TraceEnvironment
    redactions: tuple[str, ...] = ()  # rule names only, never values
    plan: dict[str, JsonValue]
    steps: tuple[StepTrace, ...] = ()
    # What past experience this run was offered (ADR-0017). None: a plain `run`, or an older run.
    recalled: RecalledContext | None = None
    request_length: int | None = None

    @model_validator(mode="after")
    def _app_id_and_how_go_together(self) -> Self:
        if (self.app_id is None) != (self.app_id_how is None):
            raise ValueError("application id and how it was obtained are recorded together")
        return self

    @model_validator(mode="after")
    def _context_and_request_go_together(self) -> Self:
        if (self.recalled is None) != (self.request_length is None):
            raise ValueError("recalled context and request length are recorded together")
        return self

    def to_task_run(self) -> TaskRun:
        """Rebuild the run so its status is recomputed from the steps, not trusted from storage."""
        return TaskRun(
            task_id=self.task_id,
            steps=tuple(
                StepRecord(
                    step_id=s.step_id,
                    capability=s.capability,
                    status=s.status,
                    agent_id=s.agent_id,
                    attempt_count=s.attempt_count,
                    attempts=tuple(a.result for a in s.attempts),
                    failure_kind=s.failure_kind,
                    message=s.message,
                    blocked_by=s.blocked_by,
                    started_at=s.started_at,
                    finished_at=s.finished_at,
                )
                for s in self.steps
            ),
        )


class RunSummary(_Frozen):
    run_id: str
    task_id: str
    status: TraceStatus
    started_at: datetime
    step_count: int


class TraceListing(_Frozen):
    runs: tuple[RunSummary, ...]
    malformed: tuple[str, ...]  # run ids that could not be read


def trace_status(status: TaskStatus) -> TraceStatus:
    return TraceStatus(status.value)
