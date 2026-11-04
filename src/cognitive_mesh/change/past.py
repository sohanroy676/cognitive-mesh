"""Earlier change drafts found by meaning, for the coder's prompt (ADR-0043).

The vector search over decisions only chooses which drafts to look at. Everything else comes from
the change events in the trace database, which code wrote as fixed phrases. A model's summary is
copied as it was recorded and stays labelled as unverified by the pack. A diff is read from git
only for a draft whose declared check succeeded, and is cut at `MAX_PAST_DIFF_CHARS`.
"""

from pathlib import Path

from cognitive_mesh.agents.context import MAX_PAST_CHANGES, MAX_PAST_DIFF_CHARS, PastChange
from cognitive_mesh.change.models import (
    ChangeEvent,
    ChangeRecord,
    EventKind,
    latest_check,
)
from cognitive_mesh.memory.models import DecisionSearchResult
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.workspace.fetch import FetchError, run_git_with

MAX_SUMMARY_CHARS = 500
_DIFF_TIMEOUT_S = 30


def _draft_diff(record: ChangeRecord, commit: str | None) -> str | None:
    """The draft's diff from its worktree, or None if it cannot be read."""
    if commit is None:
        return None
    worktree = Path(record.worktree)
    if not worktree.is_dir():
        return None
    try:
        done = run_git_with(
            ["diff", "--no-color", record.base_commit, commit],
            worktree,
            timeout_s=_DIFF_TIMEOUT_S,
        )
    except FetchError:
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout[:MAX_PAST_DIFF_CHARS]


def _past_change(
    record: ChangeRecord,
    events: list[ChangeEvent],
    decision_id: str,
    summary: str,
    similarity: float,
) -> PastChange | None:
    draft = next(
        (
            e
            for e in events
            if e.ref == decision_id and e.kind in (EventKind.APPLIED, EventKind.REFUSED)
        ),
        None,
    )
    if draft is None:
        return None
    impact = next(
        (
            e.detail
            for e in events
            if e.kind is EventKind.IMPACT and e.diff_sha256 == draft.diff_sha256
        ),
        "",
    )
    if draft.kind is EventKind.REFUSED:
        return PastChange(
            change_id=record.change_id,
            draft=draft.detail,
            impact=impact,
            summary=summary[:MAX_SUMMARY_CHARS],
            similarity=similarity,
        )
    check = latest_check(events, draft.commit) if draft.commit else None
    check_text = check.detail if check is not None else "check not run"
    diff = _draft_diff(record, draft.commit) if check_text.startswith("succeeded") else None
    return PastChange(
        change_id=record.change_id,
        draft="applied",
        impact=impact,
        check=check_text,
        summary=summary[:MAX_SUMMARY_CHARS],
        diff=diff,
        similarity=similarity,
    )


def past_changes_from_hits(
    found: DecisionSearchResult, store: TraceStore, *, limit: int = MAX_PAST_CHANGES
) -> tuple[PastChange, ...]:
    """The decisions in `found` that are change drafts, best match first, at most `limit`."""
    by_decision: dict[str, tuple[ChangeRecord, list[ChangeEvent]]] = {}
    for record, events in store.read_changes():
        for event in events:
            if event.ref is not None and event.kind in (EventKind.APPLIED, EventKind.REFUSED):
                by_decision[event.ref] = (record, events)
    items: list[PastChange] = []
    for hit in found.hits:
        owner = by_decision.get(hit.decision_id)
        if owner is None:
            continue
        item = _past_change(owner[0], owner[1], hit.decision_id, hit.summary, hit.similarity)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return tuple(items)
