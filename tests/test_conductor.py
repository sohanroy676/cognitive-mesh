"""The conductor: parsing, validation, re-drafting and exploration (ADR-0040)."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.agents import Agent, AgentContext, AgentRegistry, default_registry
from cognitive_mesh.agents.base import Capability, Handled
from cognitive_mesh.orchestrator.conduct import (
    MAX_DRAFTS,
    Conducted,
    ConductorError,
    build_payload,
    conduct,
    eligible_entries,
    explore,
)
from cognitive_mesh.providers import ProviderError, ProviderErrorKind
from conductor_support import ASSIGN, STEPS, ScriptedClient, make_inputs, reply


def _run(
    client: ScriptedClient,
    tmp_path: Path,
    *,
    registry: AgentRegistry | None = None,
    current_project: str | None = None,
) -> tuple[Conducted, list[int]]:
    drafts: list[int] = []

    def begin(n: int) -> str:
        drafts.append(n)
        return f"conducted._conductor.{n}"

    inputs = make_inputs(tmp_path, registry=registry, current_project=current_project)
    conducted = conduct(
        "explain the project file", inputs, client, task_id="conducted", begin_draft=begin
    )
    return conducted, drafts


def test_a_valid_reply_is_accepted_with_its_assignment(tmp_path: Path) -> None:
    client = ScriptedClient(reply())
    conducted, drafts = _run(client, tmp_path)
    assert drafts == [1]
    plan = conducted.plan
    assert [s.step_id for s in plan.plan.steps] == ["report", "explain"]
    assert plan.assignments["explain"].model == "alpha"
    assert plan.reasoning == "keeps it simple"
    assert conducted.accepted.refusal is None
    assert conducted.accepted.agent_task_id == "conducted._conductor.1"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("not json at all", "not valid JSON"),
        (json.dumps({"steps": STEPS}), 'exactly "steps"'),
        (json.dumps({"steps": STEPS, "assignments": ASSIGN, "reasoning": "x", "z": 1}), "exactly"),
        (reply(assignments=[]), 'exactly "steps"'),
        (
            reply(
                steps=[
                    {"step_id": "a", "capability": "unresolved_report", "depends_on": ["b"]},
                    {"step_id": "b", "capability": "unresolved_report", "depends_on": ["a"]},
                ],
                assignments={},
            ),
            "cycle",
        ),
        (reply(assignments={"explain": {"model": "ghost", "reason": "r"}}), "not an entry"),
        (reply(assignments={"explain": {"model": "nocode", "reason": "r"}}), "lacks role"),
        (reply(assignments={"explain": {"model": "remote", "reason": "r"}}), "cannot be used"),
        (reply(assignments={}), "has no assignment"),
        (
            reply(assignments={**ASSIGN, "report": {"model": "alpha", "reason": "r"}}),
            "must not be assigned",
        ),
        (reply(assignments={**ASSIGN, "nowhere": {"model": "alpha", "reason": "r"}}), "not a step"),
        (reply(assignments={"explain": {"model": "alpha", "reason": "  "}}), "reason"),
        (reply(assignments={"explain": {"model": "alpha"}}), "an assignment is"),
        (
            reply(assignments={"explain": {"model": "alpha", "reason": "r", "x": 1}}),
            "an assignment",
        ),
        (reply(reasoning="line\x07bell"), "control characters"),
        (
            reply(
                steps=[{"step_id": "_conductor", "capability": "unresolved_report"}],
                assignments={},
            ),
            "may not be called",
        ),
        (
            reply(
                steps=[
                    {
                        "step_id": "explain",
                        "capability": "explain_file",
                        "inputs": {"path": "nothere.py"},
                    }
                ]
            ),
            "not in the application",
        ),
    ],
)
def test_a_bad_reply_is_refused_twice_then_the_conductor_fails(
    tmp_path: Path, text: str, needle: str
) -> None:
    client = ScriptedClient(text, repeat=True)
    with pytest.raises(ConductorError) as caught:
        _run(client, tmp_path)
    assert len(caught.value.refusals) == MAX_DRAFTS == len(client.requests)
    assert all(needle in r for r in caught.value.refusals), caught.value.refusals


def test_a_refusal_is_sent_back_and_a_corrected_draft_is_accepted(tmp_path: Path) -> None:
    client = ScriptedClient("{broken", reply())
    conducted, drafts = _run(client, tmp_path)
    assert drafts == [1, 2]
    first, second = client.payloads()
    assert "previous_rejection" not in first and "valid JSON" in str(second["previous_rejection"])
    assert [d.refusal is None for d in conducted.drafts] == [False, True]
    assert conducted.accepted.draft_no == 2


def test_a_provider_failure_is_explicit_and_not_retried(tmp_path: Path) -> None:
    client = ScriptedClient(ProviderError(ProviderErrorKind.RATE_LIMITED), repeat=True)
    with pytest.raises(ConductorError, match="rate_limited"):
        _run(client, tmp_path)
    assert len(client.requests) == 1


def test_a_client_that_crashes_is_an_explicit_failure(tmp_path: Path) -> None:
    with pytest.raises(ConductorError, match="raised an error"):
        _run(ScriptedClient(RuntimeError("boom")), tmp_path)


def test_a_secret_in_a_reason_is_redacted_before_it_is_kept(tmp_path: Path) -> None:
    key = "AKI" + "A" + "ABCDEFGHIJKLMNOP"
    text = reply(assignments={"explain": {"model": "alpha", "reason": f"uses {key} for it"}})
    conducted, _ = _run(ScriptedClient(text), tmp_path)
    kept = conducted.plan.assignments["explain"].reason
    assert key not in kept and "uses" in kept
    assert conducted.plan.redactions


def test_the_prompt_holds_catalog_facts_and_no_key_names(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path)
    body = json.dumps(build_payload("explain the project file", inputs, None))
    assert (
        "FAR_API_KEY" not in body
        and "remote" not in json.loads(body)["models"]["explain_file"]["entries"][0]["entry"]
    )
    entries = json.loads(body)["models"]["explain_file"]["entries"]
    assert [e["entry"] for e in entries] == ["alpha", "beta", "gamma", "delta"]  # remote: no opt-in
    assert entries[0]["evidence"]["label"] == "insufficient_evidence"
    assert json.loads(body)["sample_minimum"] == 20


class _Writer(Agent):
    id = "writer"
    role = "Writer"
    description = "A fake agent whose capability changes a project."

    def __init__(self) -> None:
        from pydantic import BaseModel

        class In(BaseModel):
            project: str | None = None

        class Out(BaseModel):
            done: bool = True

        def handler(args: In, context: AgentContext) -> Handled:
            return Handled(output=Out())

        super().__init__(
            [
                Capability(
                    name="write_file",
                    description="Change a file.",
                    input_model=In,
                    output_model=Out,
                    handler=handler,
                    writes=True,
                )
            ]
        )


def _registry_with_writer() -> AgentRegistry:
    registry = default_registry()
    registry.register(_Writer())
    return registry


def test_a_write_step_may_target_only_the_current_project(tmp_path: Path) -> None:
    steps = [{"step_id": "w", "capability": "write_file", "inputs": {"project": "other"}}]
    text = reply(steps=steps, assignments={})
    with pytest.raises(ConductorError) as caught:
        _run(
            ScriptedClient(text, repeat=True),
            tmp_path,
            registry=_registry_with_writer(),
            current_project="mine",
        )
    assert all("may only change the current project" in r for r in caught.value.refusals)
    own = [{"step_id": "w", "capability": "write_file", "inputs": {"project": "mine"}}]
    conducted, _ = _run(
        ScriptedClient(reply(steps=own, assignments={})),
        tmp_path,
        registry=_registry_with_writer(),
        current_project="mine",
    )
    assert conducted.plan.plan.steps[0].step_id == "w"


def test_the_writes_flag_is_in_the_capability_specs() -> None:
    specs = {c.name: c.writes for s in _registry_with_writer().specs() for c in s.capabilities}
    assert specs["write_file"] is True
    assert not any(v for k, v in specs.items() if k != "write_file")


def test_eligible_entries_follow_catalog_order_and_skip_unusable_ones(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path)
    got = eligible_entries(inputs.catalog, "code_explainer", inputs.problem)
    assert got == ["alpha", "beta", "gamma", "delta"]
    assert eligible_entries(inputs.catalog, "conductor", inputs.problem) == ["alpha", "nocode"]


def test_exploration_is_off_at_zero_and_reproducible_otherwise(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path)
    args = ({"explain": "alpha"}, {"explain": "code_explainer"}, inputs.catalog, inputs.problem)
    assert explore("run-1", 0.0, *args) is None
    swaps = [explore(f"run-{i}", 0.2, *args) for i in range(400)]
    hit = [s for s in swaps if s is not None]
    assert 30 < len(hit) < 130  # about 20% of 400, from a hash
    assert all(s.step_id == "explain" and s.to_entry != "alpha" for s in hit)
    assert explore("run-7", 0.2, *args) == explore("run-7", 0.2, *args)
    with pytest.raises(ValueError, match="between 0 and 0.2"):
        explore("run-1", 0.5, *args)


def test_exploration_never_moves_a_step_with_no_other_usable_entry(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path)
    # the role conductor is listed by alpha and nocode; with nocode hidden alpha has no alternative
    hide_nocode = lambda entry: "hidden" if entry.roles == ("conductor",) else None  # noqa: E731
    swaps = [
        explore(f"r{i}", 0.2, {"x": "alpha"}, {"x": "conductor"}, inputs.catalog, hide_nocode)
        for i in range(300)
    ]
    assert swaps == [None] * 300


def test_the_workspace_summary_keeps_names_and_counts_only() -> None:
    from cognitive_mesh.orchestrator.conduct import workspace_summary

    data = {
        "projects": [{"name": "api", "kind": "local", "location": "C:/secret/path", "commit": "x"}],
        "detected": [{"source": "web", "target": "api", "kind": "package", "name": "lib"}],
        "api": [{"source": "web", "target": "api", "kind": "api", "path": "/users"}],
        "declared": [{"source": "a", "target": "b", "kind": "k", "note": "private note"}],
        "unresolved": [{"project": "web"}],
    }
    summary = workspace_summary(data)
    assert summary["edge_counts"] == {"api": 1, "declared": 1, "package": 1}
    assert summary["projects"] == [{"name": "api", "kind": "local"}]
    assert summary["unresolved_total"] == 1
    text = json.dumps(summary)
    assert "C:/secret/path" not in text and "private note" not in text and "/users" not in text
