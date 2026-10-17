"""Plan validation and dependency ordering. Nothing runs if the plan is invalid (ADR-0010)."""

import re
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
    by_id = {s.step_id: s for s in steps}
    for step in steps:
        _check_refs(step, by_id, registry)
    return ValidatedPlan(task_id=plan.task_id, order=_topological_order(steps))


_INPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _output_fields(registry: AgentRegistry, capability: str) -> set[str] | None:
    """Output field names every candidate agent declares, or None if a schema has no properties."""
    fields: list[set[str]] = []
    for agent in registry.find(capability):
        for cap in agent.spec().capabilities:
            props = cap.output_schema.get("properties")
            if cap.name == capability and isinstance(props, dict):
                fields.append(set(props))
    return set.intersection(*fields) if fields else None


def _check_refs(step: PlanStep, by_id: dict[str, PlanStep], registry: AgentRegistry) -> None:
    for name, ref in sorted(step.input_refs.items()):
        where = f"step {step.step_id!r} input {name!r}"
        if not _INPUT_NAME.match(name):
            raise PlanError(f"step {step.step_id!r} has an input reference with an invalid name")
        if name in step.inputs:
            raise PlanError(f"{where} is given both as a literal and as a reference")
        if ref.step_id not in step.depends_on:
            raise PlanError(f"{where} references step {ref.step_id!r}, which it does not depend on")
        fields = _output_fields(registry, by_id[ref.step_id].capability)
        if fields is not None and ref.field not in fields:
            raise PlanError(
                f"{where} references field {ref.field!r}, which step {ref.step_id!r} "
                "does not declare in its output"
            )


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
