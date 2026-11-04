"""From a request to a committed, checked change on a new branch (ADR-0042).

Per draft: one orchestrator run with one `propose_change` step (so the trace, the model call and a
model-written decision are recorded as for any run), the diff is judged by `diffparse`, applied in
a worktree, held against the contract diff, committed, and then the person's declared check runs.
A draft that is refused or whose check fails is sent back to the coder as data, up to
`MAX_REVISIONS` times. Nothing is pushed here. A check result is stored as a `check` outcome; a
run's own success never validates anything.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from cognitive_mesh.agents import AgentContext, AgentSuccess
from cognitive_mesh.agents.registry import AgentRegistry
from cognitive_mesh.change.checks import (
    FEEDBACK_CHARS,
    ISOLATION,
    CheckResult,
    CheckStatus,
    run_check,
)
from cognitive_mesh.change.diffparse import DiffError, Limits, Refusal, parse_diff
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.models import (
    ChangeEvent,
    ChangeRecord,
    ChangeStatus,
    EventKind,
    derive_status,
)
from cognitive_mesh.change.worktree import (
    Worktree,
    apply_diff,
    commit_change,
    create_worktree,
    reset_worktree,
)
from cognitive_mesh.decisions.models import DecisionError, MadeBy
from cognitive_mesh.decisions.record import observe_files, prepare_decision
from cognitive_mesh.experience.outcomes import OutcomeResult, OutcomeSource, OutcomeStore
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.identity import derive_identity
from cognitive_mesh.impact import ContractDiff, ImpactError, Verdict, contract_diff
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator import Orchestrator, TaskStatus
from cognitive_mesh.orchestrator.models import TaskPlan
from cognitive_mesh.orchestrator.planner import plan_task
from cognitive_mesh.providers.recording import CallLog
from cognitive_mesh.traces import TraceRecorder, TraceStore
from cognitive_mesh.traces.recorder import source_fingerprint, utc_now
from cognitive_mesh.workspace.access import WorkspaceAccess
from cognitive_mesh.workspace.models import CheckCommand, ProjectKind

MAX_REVISIONS: Final = 2
STEP_ID: Final = "propose"
RegistryFactory = Callable[[CallLog], AgentRegistry]
CheckRunner = Callable[[tuple[str, ...], Path, int], CheckResult]


@dataclass(frozen=True)
class ChangeRequest:
    request: str
    project: str
    files: tuple[str, ...] = ()
    limits: Limits = field(default_factory=Limits)
    worktrees: Path = Path("cogmesh-worktrees")
    base_ref: str = "HEAD"
    allow_unisolated_checks: bool = False


@dataclass(frozen=True)
class DraftReport:
    draft_no: int
    run_id: str | None
    outcome: str  # a fixed phrase: refused:<code>, coder_failed:<kind>, impact_breaking, ...
    commit: str | None = None
    diff_sha256: str | None = None
    decision_id: str | None = None
    outcome_id: int | None = None


@dataclass(frozen=True)
class ChangeResult:
    change: ChangeRecord
    status: ChangeStatus
    drafts: tuple[DraftReport, ...]
    notes: tuple[str, ...]


def _impact_text(diff: ContractDiff) -> str:
    counts = {v: diff.count(v) for v in Verdict}
    return (
        f"breaking {counts[Verdict.BREAKING]}, unknown {counts[Verdict.UNKNOWN]}, "
        f"compatible {counts[Verdict.COMPATIBLE]}, unused {counts[Verdict.UNUSED]}"
    )


def _impact_feedback(diff: ContractDiff) -> str:
    lines = ["The change breaks contracts that other projects use:"]
    for finding in diff.findings:
        if finding.verdict is Verdict.BREAKING:
            lines.append(f"- {finding.kind} {finding.subject}: {finding.reason}")
    return "\n".join(lines)[:6000]


class _Pipeline:
    def __init__(
        self,
        req: ChangeRequest,
        *,
        access: WorkspaceAccess,
        check: CheckCommand | None,
        registry_factory: RegistryFactory,
        store: TraceStore,
        outcomes: Path | None,
        now: Callable[[], datetime],
        runner: CheckRunner,
    ) -> None:
        self.req = req
        self.access = access
        self.check = check
        self.registry_factory = registry_factory
        self.store = store
        self.outcomes = outcomes
        self.now = now
        self.runner = runner
        read = access.reads[access.current]
        self.root = read.root
        self.change_id = hashlib.sha256(f"{now().isoformat()}{id(self)}".encode()).hexdigest()[:16]
        identity = derive_identity(str(self.root))
        self.app_id = identity.app_id if identity else None  # the label the runs also carry
        self.events: list[ChangeEvent] = []
        self.notes: list[str] = []

    def event(self, kind: EventKind, **fields: str | None) -> ChangeEvent:
        item = ChangeEvent(change_id=self.change_id, kind=kind, created_at=self.now(), **fields)
        self.store.add_change_event(item)
        self.events.append(item)
        return item

    # -- one draft -----------------------------------------------------------------------------

    def propose(
        self, draft_no: int, wt: Worktree, feedback: str | None
    ) -> tuple[str | None, dict[str, str] | None, str | None, str | None]:
        """Run the coder once; returns (run_id, output, decision_id, failure phrase)."""
        req = self.req
        inputs: dict[str, object] = {
            "project": req.project,
            "request": req.request,
            "files": list(req.files),
            "max_files": req.limits.max_files,
            "max_changed_lines": req.limits.max_lines,
        }
        if feedback:
            inputs["previous_rejection"] = feedback
        plan = TaskPlan.model_validate(
            {
                "task_id": f"change-{self.change_id}-d{draft_no}",
                "steps": [
                    {
                        "step_id": STEP_ID,
                        "capability": "propose_change",
                        "inputs": inputs,
                        "max_attempts": 1,
                    }
                ],
            }
        )
        calls = CallLog()
        registry = self.registry_factory(calls)
        validated = plan_task(plan, registry, allow_writes=True)
        ingestion = ingest_application(self.root)
        context = AgentContext(
            ingestion=ingestion, graph=build_dependency_graph(ingestion), workspace=self.access
        )
        recorder = TraceRecorder(self.store, now=self.now)
        run_id = recorder.begin(
            validated, ingestion, identity=derive_identity(ingestion.root, None)
        )
        run = Orchestrator(registry, now=self.now, on_attempt=calls.begin_attempt).run(
            validated, context
        )
        step = run.steps[0]
        result = step.attempts[-1] if step.attempts else None
        if run.status is not TaskStatus.SUCCEEDED or not isinstance(result, AgentSuccess):
            recorder.finish(run_id, run, calls.records)
            return run_id, None, None, f"coder_failed:{step.failure_kind or step.status.value}"
        output = {k: str(v) for k, v in result.output.items()}
        decision_id = None
        task_ids = [c.agent_task_id for c in calls.records if c.step_id == STEP_ID]
        decisions = []
        if task_ids:
            try:
                decision = prepare_decision(
                    summary=f"Draft {draft_no} of change {self.change_id}: {output['summary']}",
                    options=[f"propose draft {draft_no} as written"],
                    chosen=f"propose draft {draft_no} as written",
                    reasoning=output["reasoning"],
                    made_by=MadeBy.MODEL,
                    provider=result.model.provider if result.model else None,
                    model=result.model.model if result.model else None,
                    role="coder",
                    agent_task_id=task_ids[-1],
                    cite_files=[p for p in req.files],
                    run_id=run_id,
                    step_id=STEP_ID,
                    root=self.root,
                    view=observe_files(self.root),
                    app_id=self.app_id,
                    now=self.now(),
                )
                decisions.append(decision)
                decision_id = decision.decision_id
            except DecisionError:
                self.notes.append(f"draft {draft_no}: its decision could not be recorded")
        recorder.finish(run_id, run, calls.records, decisions)
        return run_id, output, decision_id, None

    # -- the whole change ----------------------------------------------------------------------

    def run(self) -> ChangeResult:
        req = self.req
        read = self.access.reads[self.access.current]
        if read.project.kind is not ProjectKind.LOCAL:
            raise ChangeError("the current project must be a local folder")
        self.access.require_writable(req.project)
        wt = create_worktree(self.root, req.worktrees, self.change_id, req.base_ref)
        record = ChangeRecord(
            change_id=self.change_id,
            project=req.project,
            app_id=self.app_id,
            base_branch=wt.base_branch,
            base_commit=wt.base_commit,
            branch=wt.branch,
            worktree=str(wt.toplevel),
            max_files=req.limits.max_files,
            max_lines=req.limits.max_lines,
            request_len=len(req.request),
            created_at=self.now(),
        )
        self.store.add_change(record)
        forbidden = tuple(
            _inside(read.root, other.root)
            for name, other in self.access.reads.items()
            if name != req.project and _inside(read.root, other.root)
        )
        drafts: list[DraftReport] = []
        feedback: str | None = None
        for draft_no in range(1, MAX_REVISIONS + 2):
            report, feedback = self.one_draft(draft_no, wt, feedback, forbidden)
            drafts.append(report)
            if feedback is None:
                break
        return ChangeResult(record, derive_status(self.events), tuple(drafts), tuple(self.notes))

    def refuse(
        self,
        draft_no: int,
        run_id: str | None,
        code: str,
        digest: str | None,
        decision_id: str | None,
        feedback: str | None,
    ) -> tuple[DraftReport, str | None]:
        self.event(EventKind.REFUSED, diff_sha256=digest, ref=decision_id, detail=f"refused:{code}")
        report = DraftReport(draft_no, run_id, f"refused:{code}", None, digest, decision_id)
        return report, feedback

    def one_draft(
        self, draft_no: int, wt: Worktree, feedback: str | None, forbidden: tuple[str, ...]
    ) -> tuple[DraftReport, str | None]:
        """One draft; the second value is feedback for another draft, or None to stop."""
        run_id, output, decision_id, failure = self.propose(draft_no, wt, feedback)
        if output is None:
            phrase = failure or "coder_failed"
            self.event(EventKind.REFUSED, detail=phrase)
            return DraftReport(draft_no, run_id, phrase), None
        text = output["diff"]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            parsed = parse_diff(text, self.req.limits, forbidden_prefixes=forbidden)
            apply_diff(wt, text, parsed)
        except DiffError as exc:
            message = f"The previous diff was refused ({exc.code.value}): {exc}."
            return self.refuse(draft_no, run_id, exc.code.value, digest, decision_id, message)
        try:
            impact = contract_diff(
                self.req.project,
                wt.base_commit,
                self.access.graph,
                self.access.reads,
                tree=wt.project_root,
            )
        except ImpactError as exc:
            reset_worktree(wt)
            self.notes.append(f"draft {draft_no}: the impact check could not run ({exc})")
            return self.refuse(draft_no, run_id, "impact_unavailable", digest, decision_id, None)
        self.event(EventKind.IMPACT, diff_sha256=digest, detail=_impact_text(impact))
        if impact.count(Verdict.BREAKING):
            reset_worktree(wt)
            feedback_text = _impact_feedback(impact)
            return self.refuse(
                draft_no, run_id, "impact_breaking", digest, decision_id, feedback_text
            )
        commit = commit_change(wt, self.change_id, output["summary"])
        self.event(EventKind.APPLIED, commit=commit, diff_sha256=digest, ref=decision_id)
        outcome_id, passed, check_feedback = self.run_check(wt, commit, digest, decision_id)
        report = DraftReport(draft_no, run_id, "applied", commit, digest, decision_id, outcome_id)
        if passed is False and draft_no <= MAX_REVISIONS:
            reset_worktree(wt)  # the failed commit stays in the events; the branch starts over
            return report, check_feedback
        return report, None

    def run_check(
        self, wt: Worktree, commit: str, digest: str, decision_id: str | None
    ) -> tuple[int | None, bool | None, str | None]:
        """(outcome id, passed, feedback). passed is None when nothing ran."""
        if self.check is None:
            self.event(EventKind.CHECK_SKIPPED, commit=commit, detail="no check command declared")
            return None, None, None
        if not self.req.allow_unisolated_checks:
            self.event(
                EventKind.CHECK_SKIPPED,
                commit=commit,
                detail="not run: pass --allow-unisolated-checks (the check is not isolated)",
            )
            return None, None, None
        result = self.runner(self.check.argv, wt.project_root, self.check.timeout_s)
        outcome_id = self.record_outcome(wt, result, decision_id)
        status = result.status
        self.event(
            EventKind.CHECK,
            commit=commit,
            diff_sha256=digest,
            ref=str(outcome_id) if outcome_id is not None else None,
            detail=f"{status.value}; isolation {ISOLATION}",
        )
        passed = status is CheckStatus.SUCCEEDED
        feedback = None
        if status is CheckStatus.FAILED:
            tail = result.output_tail[-FEEDBACK_CHARS:]
            feedback = (
                f"The check command failed (exit {result.exit_code}). Its output ends:\n{tail}"
            )
        elif status is not CheckStatus.SUCCEEDED:
            feedback = f"The check command did not finish ({status.value})."
        return outcome_id, passed, feedback

    def record_outcome(
        self, wt: Worktree, result: CheckResult, decision_id: str | None
    ) -> int | None:
        if self.outcomes is None or decision_id is None:
            self.notes.append("no outcomes file or no decision: the check outcome was not stored")
            return None
        outcome = {
            CheckStatus.SUCCEEDED: OutcomeResult.SUCCEEDED,
            CheckStatus.FAILED: OutcomeResult.FAILED,
        }.get(result.status, OutcomeResult.AMBIGUOUS)
        fingerprint = source_fingerprint(ingest_application(wt.project_root))
        command = " ".join(self.check.argv) if self.check else ""
        with OutcomeStore.create(self.outcomes) as outcomes:
            row = outcomes.add(
                decision_id,
                fingerprint,
                OutcomeSource.CHECK,
                outcome,
                f"{command} (exit {result.exit_code}, {result.status.value}, "
                f"isolation {ISOLATION})",
                "cognitive-mesh",
            )
        return row.id


def _inside(parent: Path, child: Path) -> str:
    """`child` relative to `parent` with '/' separators if it lies inside it, else ''."""
    try:
        rel = child.resolve().relative_to(parent.resolve())
    except ValueError:
        return ""
    text = rel.as_posix()
    return "" if text in ("", ".") else text


def make_change(
    req: ChangeRequest,
    *,
    access: WorkspaceAccess,
    check: CheckCommand | None,
    registry_factory: RegistryFactory,
    store: TraceStore,
    outcomes: Path | None,
    now: Callable[[], datetime] = utc_now,
    runner: CheckRunner = run_check,
) -> ChangeResult:
    """Make one change in a new worktree and branch. Raises `ChangeError` for what cannot start."""
    return _Pipeline(
        req,
        access=access,
        check=check,
        registry_factory=registry_factory,
        store=store,
        outcomes=outcomes,
        now=now,
        runner=runner,
    ).run()


__all__ = [
    "MAX_REVISIONS",
    "ChangeRequest",
    "ChangeResult",
    "DraftReport",
    "Refusal",
    "make_change",
]
