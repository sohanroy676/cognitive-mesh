"""Run a task set with memory off and with memory on (ADR-0043).

For every repeat and every task, the same change request goes through `make_change` twice, once per
arm, with the same model entry. The order of the two arms alternates from task to task. The
memory-on arm sees only its own earlier tasks of the same repeat. A task that stops before a
change exists is stored as aborted with a fixed reason; an unexpected exception leaves the item
unfinished, which reads back as interrupted. Nothing is dropped.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from cognitive_mesh.agents.context import PastChange, RecalledContext
from cognitive_mesh.change.checks import run_check
from cognitive_mesh.change.diffparse import Limits
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.pipeline import (
    ChangeRequest,
    ChangeResult,
    CheckRunner,
    RegistryFactory,
    make_change,
)
from cognitive_mesh.change.worktree import repo_layout
from cognitive_mesh.evaluate.errors import EvalError
from cognitive_mesh.evaluate.memory import (
    ArmPaths,
    arm_paths,
    memory_for_task,
    refresh_memory,
)
from cognitive_mesh.evaluate.store import Arm, EvalStore
from cognitive_mesh.evaluate.taskset import TaskSet, TaskSpec
from cognitive_mesh.experience.outcomes import OutcomeStoreError
from cognitive_mesh.ingestion import IngestionError
from cognitive_mesh.traces import TraceStore, TraceStoreError, utc_now
from cognitive_mesh.workspace import cli as workspace_cli
from cognitive_mesh.workspace.access import WorkspaceAccess
from cognitive_mesh.workspace.models import CheckCommand, WorkspaceError

if TYPE_CHECKING:
    from cognitive_mesh.memory import Embedder

MAX_REPEATS: Final = 5
EVAL_FILE: Final = "eval.sqlite3"
REASON_MEMORY: Final = "memory_unavailable"
REASON_CHANGE: Final = "change_not_started"
_STOPS = (ChangeError, TraceStoreError, OutcomeStoreError, IngestionError, WorkspaceError)

Progress = Callable[[str], None]


@dataclass(frozen=True)
class EvalSettings:
    out: Path
    repeats: int
    model_entry: str
    catalog_sha256: str | None
    allow_unisolated_checks: bool


def _check_for(task: TaskSpec, project: str, now: datetime) -> CheckCommand:
    return CheckCommand(
        id=0,
        project=project,
        argv=task.check,
        timeout_s=task.check_timeout_s,
        by="task set",
        created_at=now,
    )


def _prepare_out(out: Path, root: Path) -> None:
    """The folder must be new or empty and must not lie inside the project's repository."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise EvalError("the output folder must be new or empty")
    try:
        top, _ = repo_layout(root)
    except ChangeError as exc:
        raise EvalError(str(exc)) from None
    resolved = out.resolve()
    if resolved == top or top in resolved.parents:
        raise EvalError("the output folder must not be inside the project's repository")
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise EvalError("cannot create the output folder") from None


class _Run:
    def __init__(
        self,
        ts: TaskSet,
        settings: EvalSettings,
        *,
        registry_factory: RegistryFactory,
        embedder_factory: "Callable[[], Embedder]",
        runner: CheckRunner,
        now: Callable[[], datetime],
        progress: Progress,
        access: WorkspaceAccess,
    ) -> None:
        self.ts = ts
        self.settings = settings
        self.registry_factory = registry_factory
        self.embedder_factory = embedder_factory
        self.runner = runner
        self.now = now
        self.progress = progress
        self.access = access
        self._embedder: Embedder | None = None

    def embedder(self) -> "Embedder":
        if self._embedder is None:  # one model load per evaluation, not per task
            self._embedder = self.embedder_factory()
        return self._embedder

    def one_item(self, ev: EvalStore, eval_id: int, repeat: int, task: TaskSpec, arm: Arm) -> None:
        paths = arm_paths(self.settings.out, repeat, arm.value)
        paths.base.mkdir(parents=True, exist_ok=True)
        recalled: RecalledContext | None = None
        past: tuple[PastChange, ...] = ()
        memory_error = False
        started = time.monotonic()
        with TraceStore.create(paths.trace) as store:
            if arm is Arm.ON:
                try:
                    recalled, past = memory_for_task(
                        task.request,
                        access=self.access,
                        store=store,
                        paths=paths,
                        embedder=self.embedder(),
                    )
                except Exception:  # a broken memory must stop this item, not become "off"
                    memory_error = True
            prep_s = time.monotonic() - started
            claims = 0 if recalled is None else len(recalled.claims) + len(recalled.raw)
            item_id = ev.begin_item(
                eval_id,
                repeat,
                task.id,
                arm,
                claims_offered=claims,
                past_changes_offered=len(past),
                prep_s=prep_s,
                now=self.now(),
            )
            if memory_error:
                ev.finish_item(
                    item_id,
                    aborted=True,
                    change_id=None,
                    run_ids=(),
                    reason=REASON_MEMORY,
                    refresh_s=0.0,
                    now=self.now(),
                )
                return
            result = self.make(task, paths, store, recalled, past)
            if result is None:
                ev.finish_item(
                    item_id,
                    aborted=True,
                    change_id=None,
                    run_ids=(),
                    reason=REASON_CHANGE,
                    refresh_s=0.0,
                    now=self.now(),
                )
                return
            refresh_s = 0.0
            if arm is Arm.ON:
                began = time.monotonic()
                refresh_memory(store, paths, self.embedder())
                refresh_s = time.monotonic() - began
            run_ids = tuple(d.run_id for d in result.drafts if d.run_id)
            ev.finish_item(
                item_id,
                aborted=False,
                change_id=result.change.change_id,
                run_ids=run_ids,
                reason="",
                refresh_s=refresh_s,
                now=self.now(),
            )

    def make(
        self,
        task: TaskSpec,
        paths: ArmPaths,
        store: TraceStore,
        recalled: RecalledContext | None,
        past: tuple[PastChange, ...],
    ) -> ChangeResult | None:
        request = ChangeRequest(
            request=task.request,
            project=self.ts.project,
            files=task.files,
            limits=Limits(max_files=task.max_files, max_lines=task.max_lines),
            worktrees=paths.worktrees,
            allow_unisolated_checks=self.settings.allow_unisolated_checks,
            recalled=recalled,
            past_changes=past,
        )
        try:
            return make_change(
                request,
                access=self.access,
                check=_check_for(task, self.ts.project, self.now()),
                registry_factory=self.registry_factory,
                store=store,
                outcomes=paths.outcomes,
                now=self.now,
                runner=self.runner,
            )
        except _STOPS:
            return None

    def run(self) -> int:
        settings = self.settings
        with EvalStore.create(settings.out / EVAL_FILE) as ev:
            eval_id = ev.begin_eval(
                taskset_name=self.ts.name,
                taskset_sha256=self.ts.sha256,
                model_entry=settings.model_entry,
                catalog_sha256=settings.catalog_sha256,
                repeats=settings.repeats,
                task_count=len(self.ts.tasks),
                checks_allowed=settings.allow_unisolated_checks,
                now=self.now(),
            )
            for repeat in range(1, settings.repeats + 1):
                for index, task in enumerate(self.ts.tasks):
                    arms = (Arm.OFF, Arm.ON) if index % 2 == 0 else (Arm.ON, Arm.OFF)
                    for arm in arms:
                        self.progress(f"repeat {repeat} task {task.id} memory {arm.value}")
                        self.one_item(ev, eval_id, repeat, task, arm)
            ev.finish_eval(eval_id, self.now())
        return eval_id


def run_evaluation(
    ts: TaskSet,
    settings: EvalSettings,
    *,
    registry_factory: RegistryFactory,
    embedder_factory: "Callable[[], Embedder]",
    runner: CheckRunner = run_check,
    now: Callable[[], datetime] = utc_now,
    progress: Progress = lambda _line: None,
) -> int:
    """Run the whole evaluation and return its id. Raises `EvalError` for what cannot start."""
    if not 1 <= settings.repeats <= MAX_REPEATS:
        raise EvalError(f"repeats must be 1 to {MAX_REPEATS}")
    try:
        access = workspace_cli.build_access(ts.workspace, ts.project, ts.root)
    except WorkspaceError as exc:
        raise EvalError(f"workspace: {exc}") from None
    _prepare_out(settings.out, ts.root)
    return _Run(
        ts,
        settings,
        registry_factory=registry_factory,
        embedder_factory=embedder_factory,
        runner=runner,
        now=now,
        progress=progress,
        access=access,
    ).run()
