"""The evidence label of a model assignment (ADR-0040), computed by code from recorded calls.

An assignment is `recorded` when the trace database holds at least `SAMPLE_MINIMUM` calls of that
catalog entry, role and task type whose attempt outcome is known (the attempt succeeded or failed).
A call counts as a success only if the call itself returned a reply and its attempt succeeded; a
call that failed (for example rate limited before the next model took over) is a failure sample
even when a later call of the same attempt succeeded. Below the minimum the label is
`insufficient_evidence`, whatever the conductor wrote. The label is a count test,
not a ranking: it never says one model is better, and a small sample is never called proof.
"""

from collections import Counter
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.providers.recording import CONDUCTOR_ROLE, CallResult
from cognitive_mesh.traces import CallListing

SAMPLE_MINIMUM: Final = 20
RECORDED: Final = "recorded"
INSUFFICIENT: Final = "insufficient_evidence"


class Evidence(BaseModel):
    """What the trace database holds for one (catalog entry, role, task type)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    role: str
    task_type: str
    calls: int  # every recorded call
    known_outcomes: int  # calls whose attempt succeeded or failed
    attempts_success: int
    attempts_failure: int
    sample_minimum: int = SAMPLE_MINIMUM
    label: Literal["recorded", "insufficient_evidence"]

    def describe(self) -> str:
        return (
            f"{self.label}: {self.known_outcomes} call(s) with a known outcome of {self.calls} "
            f"recorded (minimum {self.sample_minimum})"
        )


class EvidenceTable:
    """Counts per (entry, role, task type) from one `CallListing`; absent means zero."""

    def __init__(self, listing: CallListing) -> None:
        self._calls: Counter[tuple[str, str, str]] = Counter()
        self._success: Counter[tuple[str, str, str]] = Counter()
        self._failure: Counter[tuple[str, str, str]] = Counter()
        for row in listing.rows:
            call = row.call
            if call.entry_id is None or call.role == CONDUCTOR_ROLE:
                continue
            key = (call.entry_id, call.role, call.task_type)
            self._calls[key] += 1
            if row.attempt_status is None:
                continue
            if row.attempt_status == "success" and call.result is CallResult.OK:
                self._success[key] += 1
            else:
                self._failure[key] += 1

    def get(self, entry_id: str, role: str, task_type: str) -> Evidence:
        key = (entry_id, role, task_type)
        known = self._success[key] + self._failure[key]
        return Evidence(
            entry_id=entry_id,
            role=role,
            task_type=task_type,
            calls=self._calls[key],
            known_outcomes=known,
            attempts_success=self._success[key],
            attempts_failure=self._failure[key],
            label=RECORDED if known >= SAMPLE_MINIMUM else INSUFFICIENT,
        )
