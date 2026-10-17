"""Plan validation and dependency ordering. Nothing runs if the plan is invalid (ADR-0010)."""

from dataclasses import dataclass

from cognitive_mesh.agents import AgentRegistry
from cognitive_mesh.orchestrator.models import MAX_STEPS, PlanError, PlanStep, TaskPlan


@dataclass(frozen=True)
class ValidatedPlan:
    """A plan whose steps are in a valid execution order. Only `plan_task` builds one."""

    task_id: str
    order: tuple[PlanStep, ...]


def plan_task(plan: TaskPlan, registry: AgentRegistry) -> ValidatedPlan:
    steps = plan.steps
    if not steps:
        raise PlanError("the plan has no steps")
    if len(steps) > MAX_STEPS:
        raise PlanError(f"the plan has more than {MAX_STEPS} steps")
    ids = [s.step_id for s in steps]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PlanError(f"duplicate step ids: {', '.join(duplicates)}")
    known = set(ids)
    for step in steps:
        if step.step_id in step.depends_on:
            raise PlanError(f"step {step.step_id!r} depends on itself")
        missing = sorted(set(step.depends_on) - known)
        if missing:
            raise PlanError(f"step {step.step_id!r} depends on unknown steps: {', '.join(missing)}")
        if not registry.find(step.capability):
            raise PlanError(
                f"step {step.step_id!r} needs capability {step.capability!r}, "
                "which no registered agent declares"
            )
    return ValidatedPlan(task_id=plan.task_id, order=_topological_order(steps))


def _topological_order(steps: tuple[PlanStep, ...]) -> tuple[PlanStep, ...]:
    """Kahn's algorithm; the earliest-declared ready step goes first, so the order is stable."""
    position = {s.step_id: i for i, s in enumerate(steps)}
    waiting = {s.step_id: set(s.depends_on) for s in steps}
    ordered: list[PlanStep] = []
    while waiting:
        ready = sorted((i for i, deps in waiting.items() if not deps), key=position.__getitem__)
        if not ready:
            raise PlanError(f"dependency cycle among steps: {', '.join(sorted(waiting))}")
        chosen = ready[0]
        ordered.append(steps[position[chosen]])
        del waiting[chosen]
        for deps in waiting.values():
            deps.discard(chosen)
    return tuple(ordered)
