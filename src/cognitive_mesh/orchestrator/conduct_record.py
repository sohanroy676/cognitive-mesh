"""The conductor's decisions (ADR-0040 point 9), built in memory and stored by `finish_run`.

One plan decision, then one decision per model step. All are `MadeBy.MODEL` (so `model_unverified`):
the conductor's reasons are text a model wrote, and a `recorded` evidence label is a count, never
proof. They name the conductor's call as `agent_task_id`, so the store requires that call to be in
the same run. Summaries are written by code from the validated plan.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from cognitive_mesh.decisions.models import MAX_OPTION, MAX_OPTIONS, MAX_SUMMARY, Decision, MadeBy
from cognitive_mesh.decisions.record import FileView, prepare_decision
from cognitive_mesh.orchestrator.conduct import Conducted, Swap, eligible_entries
from cognitive_mesh.orchestrator.models import PlanStep
from cognitive_mesh.orchestrator.routing_evidence import EvidenceTable
from cognitive_mesh.providers.catalog import Catalog, CatalogEntry
from cognitive_mesh.providers.recording import CONDUCTOR_ROLE

MAX_CITED_CLAIMS = 40
MAX_CITED_FILES = 9  # with the claims this stays under the 50-citation limit of a decision


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def final_assignments(conducted: Conducted, swap: Swap | None) -> dict[str, str]:
    """step id -> the catalog entry that will serve it, after any exploration swap."""
    chosen = {sid: a.model for sid, a in conducted.plan.assignments.items()}
    if swap is not None:
        chosen[swap.step_id] = swap.to_entry
    return chosen


def build_conductor_decisions(
    *,
    run_id: str,
    task_id: str,
    conducted: Conducted,
    conductor_entry: str,
    conductor_provider: str,
    conductor_model: str,
    catalog: Catalog,
    problem: Callable[[CatalogEntry], str | None],
    evidence: EvidenceTable,
    capability_roles: Mapping[str, str],
    steps: Sequence[PlanStep],
    swap: Swap | None,
    claim_ids: Sequence[str],
    view: FileView,
    app_id: str | None,
    now: datetime,
) -> list[Decision]:
    """The plan decision first, then the per-step decisions that cite it."""
    plan = conducted.plan
    agent_task_id = conducted.accepted.agent_task_id
    who: dict[str, Any] = {
        "made_by": MadeBy.MODEL,
        "provider": conductor_provider,
        "model": conductor_model,
        "role": CONDUCTOR_ROLE,
        "entry_id": conductor_entry,
        "agent_task_id": agent_task_id,
        "run_id": run_id,
        "view": view,
        "app_id": app_id,
    }
    model_steps = [s for s in steps if s.capability in capability_roles]
    options = [
        _cut(
            f"draft {d.draft_no}: "
            + (f"refused ({d.refusal})" if d.refusal is not None else "accepted"),
            MAX_OPTION,
        )
        for d in conducted.drafts
    ]
    files = sorted(
        {
            str(s.inputs["path"]).removeprefix("module:")
            for s in steps
            if s.capability == "explain_file" and "path" in s.inputs
        }
    )
    cited_files = [f for f in files if f in view.hashes][:MAX_CITED_FILES]
    plan_decision = prepare_decision(
        summary=_cut(
            f"Conductor plan for task {task_id}: {len(steps)} step(s), "
            f"{len(model_steps)} with a model",
            MAX_SUMMARY,
        ),
        options=options,
        chosen=f"draft {conducted.accepted.draft_no}",
        reasoning=plan.reasoning,
        cite_nodes=list(dict.fromkeys(claim_ids))[:MAX_CITED_CLAIMS],
        cite_files=cited_files,
        now=now,
        **who,
    )
    decisions = [plan_decision]
    final = final_assignments(conducted, swap)
    for n, step in enumerate(model_steps, start=1):
        role = capability_roles[step.capability]
        entry_id = final[step.step_id]
        ev = evidence.get(entry_id, role, step.capability)
        swapped = swap is not None and swap.step_id == step.step_id
        note = (
            f"; exploration moved it from {swap.from_entry} (rate {swap.rate})"
            if swap is not None and swapped
            else ""
        )
        reason = plan.assignments[step.step_id].reason
        decisions.append(
            prepare_decision(
                summary=_cut(
                    f"Assign {entry_id} to role {role} for {step.capability} (step "
                    f"{step.step_id}); {ev.describe()}{note}",
                    MAX_SUMMARY,
                ),
                options=_entry_options(catalog, role, problem, entry_id),
                chosen=entry_id,
                reasoning=(
                    f"[exploration swap from {swap.from_entry} to {swap.to_entry}] "
                    f"The conductor's reason for {swap.from_entry}: {reason}"
                    if swap is not None and swapped
                    else reason
                ),
                cite_decisions=[plan_decision.decision_id],
                step_id=step.step_id,
                now=now + timedelta(microseconds=n),
                **who,
            )
        )
    return decisions


def _entry_options(
    catalog: Catalog,
    role: str,
    problem: Callable[[CatalogEntry], str | None],
    chosen: str,
) -> list[str]:
    options = eligible_entries(catalog, role, problem)[:MAX_OPTIONS]
    if chosen not in options:
        options = [*options[: MAX_OPTIONS - 1], chosen]
    return options


def describe_assignments(
    conducted: Conducted,
    swap: Swap | None,
    steps: Sequence[PlanStep],
    capability_roles: Mapping[str, str],
    evidence: EvidenceTable,
) -> list[dict[str, object]]:
    """What a person sees for each model step: role, model, reason, evidence, any swap."""
    final = final_assignments(conducted, swap)
    rows: list[dict[str, object]] = []
    for step in steps:
        role = capability_roles.get(step.capability)
        if role is None:
            continue
        ev = evidence.get(final[step.step_id], role, step.capability)
        assignment = conducted.plan.assignments[step.step_id]
        rows.append(
            {
                "step_id": step.step_id,
                "capability": step.capability,
                "role": role,
                "model": final[step.step_id],
                "conductor_choice": assignment.model,
                "explored": swap is not None and swap.step_id == step.step_id,
                "reason": assignment.reason,
                "reason_basis": "model_unverified",
                "evidence": ev.model_dump(mode="json", exclude={"entry_id", "role", "task_type"}),
            }
        )
    return rows
