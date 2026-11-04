"""Records of a change (ADR-0042): one immutable header and an append-only list of events.

The status of a change is derived from its events and never stored. Text fields are fixed phrases
written by code (a refusal code, a count, a status word); model text never goes into an event.
"""

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

MAX_DETAIL: Final = 500


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EventKind(StrEnum):
    # a draft was applied and committed: `commit`, `diff_sha256`, and `ref` = the draft's decision
    APPLIED = "applied"
    REFUSED = "refused"  # a draft was refused; `detail` has the fixed reason
    IMPACT = "impact"  # the contract diff ran on a draft; `detail` has the counts
    CHECK = "check"  # the declared check ran; `ref` is the outcome id
    CHECK_SKIPPED = "check_skipped"  # nothing ran; `detail` says why
    APPROVED = "approved"  # a person approved `commit` and `diff_sha256`; `ref` is the decision
    PUSHED = "pushed"
    PUSH_FAILED = "push_failed"
    PR_OPENED = "pr_opened"  # `ref` is the pull request address
    PR_FAILED = "pr_failed"
    DISCARDED = "discarded"  # the worktree was removed; the branch stays


class ChangeRecord(_Frozen):
    change_id: str
    project: str
    app_id: str | None
    base_branch: str | None
    base_commit: str
    branch: str
    worktree: str
    max_files: int
    max_lines: int
    request_len: int
    created_at: datetime


class ChangeEvent(_Frozen):
    id: int | None = None
    change_id: str
    kind: EventKind
    commit: str | None = None
    diff_sha256: str | None = None
    ref: str | None = None
    detail: str = Field(default="", max_length=MAX_DETAIL)
    created_at: datetime


class ChangeStatus(StrEnum):
    NO_DRAFT = "no_draft"  # nothing was applied (all drafts refused or the coder failed)
    UNCHECKED = "unchecked"  # applied, no passing or failing check recorded for the commit
    CHECK_FAILED = "check_failed"
    CHECK_PASSED = "check_passed"
    APPROVED = "approved"
    PUSHED = "pushed"
    PR_OPENED = "pr_opened"
    DISCARDED = "discarded"


def current_commit(events: list[ChangeEvent]) -> ChangeEvent | None:
    """The newest applied draft: the commit the branch holds."""
    applied = [e for e in events if e.kind is EventKind.APPLIED]
    return applied[-1] if applied else None


def latest_check(events: list[ChangeEvent], commit: str) -> ChangeEvent | None:
    found = [e for e in events if e.kind is EventKind.CHECK and e.commit == commit]
    return found[-1] if found else None


def approval_for(events: list[ChangeEvent], commit: str, diff_sha256: str) -> ChangeEvent | None:
    """An approval that names exactly this commit and diff, or None (a stale one does not count)."""
    found = [
        e
        for e in events
        if e.kind is EventKind.APPROVED and e.commit == commit and e.diff_sha256 == diff_sha256
    ]
    return found[-1] if found else None


def derive_status(events: list[ChangeEvent]) -> ChangeStatus:
    kinds = {e.kind for e in events}
    if EventKind.DISCARDED in kinds:
        return ChangeStatus.DISCARDED
    head = current_commit(events)
    if head is None or head.commit is None or head.diff_sha256 is None:
        return ChangeStatus.NO_DRAFT
    after = [e for e in events if e.commit == head.commit]
    if any(e.kind is EventKind.PR_OPENED for e in after):
        return ChangeStatus.PR_OPENED
    if any(e.kind is EventKind.PUSHED for e in after):
        return ChangeStatus.PUSHED
    if approval_for(events, head.commit, head.diff_sha256) is not None:
        return ChangeStatus.APPROVED
    check = latest_check(events, head.commit)
    if check is None:
        return ChangeStatus.UNCHECKED
    return (
        ChangeStatus.CHECK_PASSED
        if check.detail.startswith("succeeded")
        else (ChangeStatus.CHECK_FAILED)
    )
