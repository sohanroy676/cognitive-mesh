"""The report of an evaluation (ADR-0043), computed from the stores on every call, never stored.

Counts only. Every item appears, including aborted and interrupted ones. Pairs where either side did
not finish, or where the calls were not all answered by the evaluation's one model entry, are listed
and left out of the paired counts. A passing check says only that the declared command passed.
"""

import statistics
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.change.models import ChangeEvent, ChangeStatus, EventKind, derive_status
from cognitive_mesh.evaluate.errors import EvalError
from cognitive_mesh.evaluate.memory import arm_paths
from cognitive_mesh.evaluate.runner import EVAL_FILE
from cognitive_mesh.evaluate.store import Arm, EvalItem, EvalStore, ItemState
from cognitive_mesh.orchestrator.routing_evidence import SAMPLE_MINIMUM
from cognitive_mesh.traces import TraceStore, TraceStoreError

CHECK_NOTE: Final = (
    "A passing check validates only that the declared command passed; it does not show the change "
    "is good. Memory-off and memory-on results are counts of what was measured, not a ranking."
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ItemResult(_Frozen):
    repeat: int
    task_id: str
    arm: str
    state: str  # finished | aborted | interrupted
    outcome: str  # the change status, or the state when no change exists
    reason: str
    drafts: int
    impact_refusals: int
    other_refusals: int
    coder_failures: int
    check_failures: int
    claims_offered: int
    past_changes_offered: int
    calls: int
    tokens_known: int
    calls_without_tokens: int
    entries: tuple[str, ...]
    wall_s: float | None
    prep_s: float
    refresh_s: float


class ArmSummary(_Frozen):
    arm: str
    items: int
    finished: int
    aborted: int
    interrupted: int
    check_passed: int
    check_failed: int
    check_not_run: int
    no_draft: int
    drafts: int
    impact_refusals: int
    other_refusals: int
    coder_failures: int
    check_failures: int
    tokens_known: int
    calls_without_tokens: int
    median_wall_s: float | None
    median_prep_s: float | None


class Pair(_Frozen):
    repeat: int
    task_id: str
    off: str
    on: str
    counted: bool
    why_not: str = ""


class PairedCounts(_Frozen):
    counted: int
    excluded: int
    both_passed: int
    only_on_passed: int
    only_off_passed: int
    neither_passed: int


class EvalReport(_Frozen):
    eval_id: int
    taskset_name: str
    taskset_sha256: str
    model_entry: str
    repeats: int
    task_count: int
    checks_allowed: bool
    finished: bool
    arms: tuple[ArmSummary, ...]
    items: tuple[ItemResult, ...]
    pairs: tuple[Pair, ...]
    paired: PairedCounts
    sample_line: str
    note: str = CHECK_NOTE


def _item_result(item: EvalItem, events: list[ChangeEvent], store: TraceStore | None) -> ItemResult:
    drafts = sum(1 for e in events if e.kind in (EventKind.APPLIED, EventKind.REFUSED))
    refused = [e.detail for e in events if e.kind is EventKind.REFUSED]
    coder = sum(1 for d in refused if d.startswith("coder_failed"))
    impact = sum(1 for d in refused if d == "refused:impact_breaking")
    other = sum(1 for d in refused if d.startswith("refused:") and d != "refused:impact_breaking")
    check_failures = sum(
        1 for e in events if e.kind is EventKind.CHECK and not e.detail.startswith("succeeded")
    )
    calls = known = unknown = 0
    entries: set[str] = set()
    if store is not None and item.run_ids:
        wanted = set(item.run_ids)
        for row in store.call_rows().rows:
            if row.run_id not in wanted:
                continue
            calls += 1
            if row.call.entry_id is not None:
                entries.add(row.call.entry_id)
            prompt, completion = row.call.prompt_tokens, row.call.completion_tokens
            if prompt is None or completion is None:
                unknown += 1
            else:
                known += prompt + completion
    if item.state is ItemState.FINISHED and events:
        outcome = derive_status(events).value
    else:
        outcome = item.state.value
    wall = (item.finished_at - item.started_at).total_seconds() if item.finished_at else None
    return ItemResult(
        repeat=item.repeat,
        task_id=item.task_id,
        arm=item.arm.value,
        state=item.state.value,
        outcome=outcome,
        reason=item.reason,
        drafts=drafts,
        impact_refusals=impact,
        other_refusals=other,
        coder_failures=coder,
        check_failures=check_failures,
        claims_offered=item.claims_offered,
        past_changes_offered=item.past_changes_offered,
        calls=calls,
        tokens_known=known,
        calls_without_tokens=unknown,
        entries=tuple(sorted(entries)),
        wall_s=wall,
        prep_s=item.prep_s,
        refresh_s=item.refresh_s,
    )


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _summary(arm: Arm, rows: list[ItemResult]) -> ArmSummary:
    def count(outcome: str) -> int:
        return sum(1 for r in rows if r.outcome == outcome)

    return ArmSummary(
        arm=arm.value,
        items=len(rows),
        finished=sum(1 for r in rows if r.state == ItemState.FINISHED.value),
        aborted=sum(1 for r in rows if r.state == ItemState.ABORTED.value),
        interrupted=sum(1 for r in rows if r.state == ItemState.INTERRUPTED.value),
        check_passed=count(ChangeStatus.CHECK_PASSED.value),
        check_failed=count(ChangeStatus.CHECK_FAILED.value),
        check_not_run=count(ChangeStatus.UNCHECKED.value),
        no_draft=count(ChangeStatus.NO_DRAFT.value),
        drafts=sum(r.drafts for r in rows),
        impact_refusals=sum(r.impact_refusals for r in rows),
        other_refusals=sum(r.other_refusals for r in rows),
        coder_failures=sum(r.coder_failures for r in rows),
        check_failures=sum(r.check_failures for r in rows),
        tokens_known=sum(r.tokens_known for r in rows),
        calls_without_tokens=sum(r.calls_without_tokens for r in rows),
        median_wall_s=_median([r.wall_s for r in rows if r.wall_s is not None]),
        median_prep_s=_median([r.prep_s for r in rows]),
    )


def _pairs(rows: list[ItemResult], model_entry: str) -> tuple[tuple[Pair, ...], PairedCounts]:
    by_key: dict[tuple[int, str], dict[str, ItemResult]] = {}
    for r in rows:
        by_key.setdefault((r.repeat, r.task_id), {})[r.arm] = r
    pairs: list[Pair] = []
    both = only_on = only_off = neither = 0
    for (repeat, task_id), arms in sorted(by_key.items()):
        off, on = arms.get(Arm.OFF.value), arms.get(Arm.ON.value)
        why = ""
        if off is None or on is None:
            why = "one arm has no record"
        elif off.state != ItemState.FINISHED.value or on.state != ItemState.FINISHED.value:
            why = "an arm did not finish"
        elif any(set(r.entries) - {model_entry} for r in (off, on)):
            why = "mixed models"
        pairs.append(
            Pair(
                repeat=repeat,
                task_id=task_id,
                off=off.outcome if off else "missing",
                on=on.outcome if on else "missing",
                counted=not why,
                why_not=why,
            )
        )
        if why or off is None or on is None:
            continue
        off_ok = off.outcome == ChangeStatus.CHECK_PASSED.value
        on_ok = on.outcome == ChangeStatus.CHECK_PASSED.value
        if off_ok and on_ok:
            both += 1
        elif on_ok:
            only_on += 1
        elif off_ok:
            only_off += 1
        else:
            neither += 1
    counted = both + only_on + only_off + neither
    return tuple(pairs), PairedCounts(
        counted=counted,
        excluded=len(pairs) - counted,
        both_passed=both,
        only_on_passed=only_on,
        only_off_passed=only_off,
        neither_passed=neither,
    )


def sample_line(counted: int) -> str:
    if counted < SAMPLE_MINIMUM:
        return (
            f"{counted} paired task(s); minimum {SAMPLE_MINIMUM}; "
            "too few paired tasks to conclude anything"
        )
    return (
        f"{counted} paired task(s); the minimum of {SAMPLE_MINIMUM} is met, "
        "but no statistical test was run and these are counts only"
    )


def build_report(out: Path, eval_id: int | None = None) -> EvalReport:
    """Read the evaluation file and each arm's trace database. Raises `EvalError`."""
    with EvalStore.open_existing(out / EVAL_FILE) as ev:
        headers = ev.evals()
        if not headers:
            raise EvalError("the evaluation file holds no evaluation")
        if eval_id is None:
            header = headers[-1]
        else:
            found = [h for h in headers if h.id == eval_id]
            if not found:
                raise EvalError("no evaluation has that id")
            header = found[0]
        items = ev.items(header.id)
    stores: dict[tuple[int, str], TraceStore | None] = {}
    changes: dict[tuple[int, str], dict[str, list[ChangeEvent]]] = {}
    results: list[ItemResult] = []
    try:
        for item in items:
            key = (item.repeat, item.arm.value)
            if key not in stores:
                path = arm_paths(out, *key).trace
                try:
                    opened = TraceStore.open_existing(path) if path.is_file() else None
                except TraceStoreError:
                    raise EvalError("cannot read an arm's trace database") from None
                stores[key] = opened
                changes[key] = {r.change_id: e for r, e in opened.read_changes()} if opened else {}
            events = changes[key].get(item.change_id or "", [])
            results.append(_item_result(item, events, stores[key]))
    except TraceStoreError:
        raise EvalError("cannot read an arm's trace database") from None
    finally:
        for opened in stores.values():
            if opened is not None:
                opened.close()
    arms = tuple(
        _summary(arm, [r for r in results if r.arm == arm.value]) for arm in (Arm.OFF, Arm.ON)
    )
    pairs, paired = _pairs(results, header.model_entry)
    return EvalReport(
        eval_id=header.id,
        taskset_name=header.taskset_name,
        taskset_sha256=header.taskset_sha256,
        model_entry=header.model_entry,
        repeats=header.repeats,
        task_count=header.task_count,
        checks_allowed=header.checks_allowed,
        finished=header.finished_at is not None,
        arms=arms,
        items=tuple(results),
        pairs=pairs,
        paired=paired,
        sample_line=sample_line(paired.counted),
    )


def render_text(report: EvalReport) -> str:
    lines = [
        f"evaluation {report.eval_id}: task set {report.taskset_name} "
        f"(sha256 {report.taskset_sha256[:12]}), model {report.model_entry}",
        f"{report.task_count} task(s) x {report.repeats} repeat(s) x 2 arms"
        + ("" if report.finished else "  [NOT FINISHED: the run stopped early]"),
        "checks: " + ("allowed (not isolated)" if report.checks_allowed else "not run"),
        "",
    ]
    for a in report.arms:
        lines += [
            f"memory {a.arm}: {a.items} item(s): {a.finished} finished, {a.aborted} aborted, "
            f"{a.interrupted} interrupted",
            f"  check passed {a.check_passed}, failed {a.check_failed}, "
            f"not run {a.check_not_run}, no draft {a.no_draft}",
            f"  drafts {a.drafts}, impact refusals {a.impact_refusals}, "
            f"other refusals {a.other_refusals}, coder failures {a.coder_failures}, "
            f"failed check runs {a.check_failures}",
            f"  tokens {a.tokens_known} known ({a.calls_without_tokens} call(s) without counts), "
            f"median task {a.median_wall_s}s, median context preparation {a.median_prep_s}s",
        ]
    lines.append("")
    lines.append("pairs (repeat, task: off -> on):")
    for p in report.pairs:
        tail = "" if p.counted else f"  [not counted: {p.why_not}]"
        lines.append(f"  r{p.repeat} {p.task_id}: {p.off} -> {p.on}{tail}")
    c = report.paired
    lines += [
        "",
        f"paired counts: both passed {c.both_passed}, only memory on {c.only_on_passed}, "
        f"only memory off {c.only_off_passed}, neither {c.neither_passed}; "
        f"{c.excluded} pair(s) not counted",
        report.sample_line,
        report.note,
    ]
    return "\n".join(lines)
