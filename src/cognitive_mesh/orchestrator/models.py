"""Plan and run records for the orchestrator (ADR-0010)."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from cognitive_mesh.agents import AgentResult

MAX_STEPS = 100
MAX_ATTEMPTS_CAP = 5

_ID = r"^[A-Za-z0-9_-]{1,48}$"


class PlanError(Exception):
    """The plan is invalid. Raised before anything runs; names ids only, never input values."""


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"  # a prerequisite failed or was blocked
    NOT_RUN = "not_run"  # the run budget ran out first


class TaskStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class OrchestratorFailureKind(StrEnum):
    """Failures the orchestrator detects itself; agent failures keep their `FailureKind` value."""

    INVALID_RESULT = "invalid_result"
    AGENT_RAISED = "agent_raised"
    NO_AGENT = "no_agent"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlanStep(_Frozen):
    step_id: str = Field(pattern=_ID)
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    inputs: dict[str, JsonValue] = Field(default_factory=dict)  # literal; untrusted
    depends_on: tuple[str, ...] = ()
    max_attempts: int | None = Field(default=None, ge=1, le=MAX_ATTEMPTS_CAP)


class TaskPlan(_Frozen):
    task_id: str = Field(pattern=_ID)
    steps: tuple[PlanStep, ...]


class RetryPolicy(_Frozen):
    max_attempts: int = Field(default=2, ge=1, le=MAX_ATTEMPTS_CAP)
    backoff_s: float = Field(default=0.0, ge=0, le=60)


class StepRecord(_Frozen):
    step_id: str
    capability: str
    status: StepStatus
    agent_id: str | None = None
    attempt_count: int = 0  # includes attempts whose result was invalid
    attempts: tuple[AgentResult, ...] = ()  # valid results only, in order
    failure_kind: str | None = None
    message: str | None = None
    blocked_by: tuple[str, ...] = ()
    started_at: datetime | None = None  # set only when the orchestrator has a `now` clock
    finished_at: datetime | None = None


class TaskRun(_Frozen):
    task_id: str
    steps: tuple[StepRecord, ...]  # in execution order

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> TaskStatus:
        """Derived from the steps, so it cannot disagree with them."""
        statuses = {s.status for s in self.steps}
        if statuses & {StepStatus.FAILED, StepStatus.BLOCKED}:
            return TaskStatus.FAILED
        if StepStatus.NOT_RUN in statuses or not self.steps:
            return TaskStatus.INCOMPLETE
        return TaskStatus.SUCCEEDED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_attempts(self) -> int:
        return sum(s.attempt_count for s in self.steps)
