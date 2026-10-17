"""Contract tests that apply to every agent: capabilities, validation, failures, registry."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from cognitive_mesh.agents import (
    Agent,
    AgentContext,
    AgentFailure,
    AgentFault,
    AgentRegistry,
    AgentRegistryError,
    AgentResult,
    AgentSuccess,
    AgentTask,
    Capability,
    DependencyAnalyst,
    FailureKind,
    Handled,
    default_registry,
)
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.providers import ModelRequest, ModelResponse

FIXTURES = Path(__file__).parent / "fixtures"
HOSTILE = "SECRET-MARKER-<script>\x1b[31m"

# Controlled, valid inputs for every capability any default agent declares.
VALID_INPUTS: dict[str, dict[str, object]] = {
    "dependencies_of": {"node_id": "module:src/dep_app/a.py"},
    "unresolved_report": {},
    "explain_file": {"path": "pyproject.toml"},
}


class _GoodClient:
    provider = "fake"
    model = "fake-1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        text = json.dumps({"summary": "ok", "symbols": []})
        return ModelResponse(provider=self.provider, model=self.model, text=text)


class _Echo(BaseModel):
    value: str = ""


def _context() -> AgentContext:
    graph_result = ingest_application(FIXTURES / "dep_app")
    return AgentContext(
        ingestion=ingest_application(FIXTURES / "sample_app"),
        graph=build_dependency_graph(graph_result),
    )


def _task(capability: str, inputs: dict[str, object] | None = None) -> AgentTask:
    return AgentTask.model_validate(
        {"task_id": "t1", "capability": capability, "inputs": inputs or {}}
    )


class _Fake(Agent):
    id = "fake"
    role = "Fake"
    description = "test double"

    def __init__(self, handler: object) -> None:
        super().__init__(
            [Capability("do", "does it", _Echo, _Echo, handler)]  # type: ignore[arg-type]
        )


def test_every_declared_capability_runs_with_controlled_inputs() -> None:
    """No capability is declared without a working handler (Fix-if: claims it can't do)."""
    registry = default_registry(_GoodClient())
    context = _context()
    seen = set()
    for spec in registry.specs():
        for cap in spec.capabilities:
            seen.add(cap.name)
            result = registry.get(spec.id).run(_task(cap.name, VALID_INPUTS[cap.name]), context)
            assert isinstance(result, AgentSuccess), (spec.id, cap.name, result)
            assert result.agent_id == spec.id
            assert result.capability == cap.name
            assert (result.model is not None) == cap.uses_model
    assert seen == set(VALID_INPUTS)


def test_specs_are_discoverable_and_sorted() -> None:
    specs = default_registry().specs()
    assert [s.id for s in specs] == ["code_explainer", "dependency_analyst"]
    for spec in specs:
        assert spec.role and spec.description
        for cap in spec.capabilities:
            assert cap.input_schema["type"] == "object"
            assert cap.output_schema["type"] == "object"
    explainer = specs[0].capabilities[0]
    assert explainer.uses_model is True
    assert all(not c.uses_model for c in specs[1].capabilities)
    json.dumps([s.model_dump(mode="json") for s in specs])


def test_unsupported_capability_is_reported_clearly() -> None:
    result = DependencyAnalyst().run(_task("write_poetry"), _context())
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.UNSUPPORTED_CAPABILITY
    assert "dependencies_of" in result.message and "unresolved_report" in result.message
    assert "write_poetry" not in result.message


@pytest.mark.parametrize(
    "inputs",
    [
        {},  # missing node_id
        {"node_id": 5},  # wrong type
        {"node_id": "x", "transitive": "yes"},  # strict: no coercion
        {"node_id": "x", "limit": 0},
        {"node_id": "x", "limit": 10_000},
        {"node_id": ""},
        {"node_id": "x" * 501},
        {"node_id": "x", HOSTILE: 1},  # unknown key
    ],
    ids=["missing", "type", "bool", "limit0", "limitbig", "empty", "long", "extra"],
)
def test_invalid_inputs_fail_without_echoing_values(inputs: dict[str, object]) -> None:
    result = DependencyAnalyst().run(_task("dependencies_of", inputs), _context())
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.INVALID_INPUT
    assert not result.retryable
    assert "SECRET-MARKER" not in result.message
    assert "\x1b" not in result.message


def test_hostile_values_never_reach_messages() -> None:
    result = DependencyAnalyst().run(_task("dependencies_of", {"node_id": HOSTILE}), _context())
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.INVALID_INPUT  # unknown node
    assert "SECRET-MARKER" not in result.model_dump_json()


def test_task_ids_and_capability_names_are_restricted() -> None:
    for bad in [{"task_id": HOSTILE}, {"capability": HOSTILE}, {"task_id": ""}]:
        data = {"task_id": "t", "capability": "do", "inputs": {}} | bad
        with pytest.raises(ValidationError):
            AgentTask.model_validate(data)
    with pytest.raises(ValidationError):
        AgentTask.model_validate({"task_id": "t", "capability": "do", "inputs": "not-a-dict"})


def test_missing_context_is_explicit() -> None:
    result = DependencyAnalyst().run(
        _task("dependencies_of", VALID_INPUTS["dependencies_of"]), AgentContext()
    )
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.MISSING_CONTEXT


def test_unexpected_exception_becomes_internal_error_without_its_text() -> None:
    def boom(args: _Echo, context: AgentContext) -> Handled:
        raise RuntimeError(HOSTILE)

    result = _Fake(boom).run(_task("do"), AgentContext())
    assert isinstance(result, AgentFailure)
    assert result.kind is FailureKind.INTERNAL_ERROR
    assert "RuntimeError" in result.message
    assert "SECRET-MARKER" not in result.message


def test_handler_fault_becomes_its_failure_with_retryable() -> None:
    def fault(args: _Echo, context: AgentContext) -> Handled:
        raise AgentFault(FailureKind.PROVIDER_TIMEOUT, "slow", retryable=True)

    result = _Fake(fault).run(_task("do"), AgentContext())
    assert isinstance(result, AgentFailure)
    assert (result.kind, result.retryable) == (FailureKind.PROVIDER_TIMEOUT, True)


def test_handler_returning_wrong_type_is_never_success() -> None:
    class Other(BaseModel):
        x: int = 1

    def wrong(args: _Echo, context: AgentContext) -> Handled:
        return Handled(Other())

    def not_handled(args: _Echo, context: AgentContext) -> object:
        return {"value": "x"}

    for handler in (wrong, not_handled):
        result = _Fake(handler).run(_task("do"), AgentContext())
        assert isinstance(result, AgentFailure)
        assert result.kind is FailureKind.INTERNAL_ERROR


def test_agent_definition_errors_are_rejected() -> None:
    class NoCaps(Agent):
        id, role, description = "a", "b", "c"

    class DupCaps(Agent):
        id, role, description = "a", "b", "c"

    def h(args: _Echo, context: AgentContext) -> Handled:
        return Handled(_Echo())

    with pytest.raises(ValueError, match="capability"):
        NoCaps([])
    cap = Capability("x", "d", _Echo, _Echo, h)
    with pytest.raises(ValueError, match="unique"):
        DupCaps([cap, cap])


def test_results_round_trip_through_json() -> None:
    adapter: TypeAdapter[AgentResult] = TypeAdapter(AgentResult)
    ok = DependencyAnalyst().run(
        _task("dependencies_of", VALID_INPUTS["dependencies_of"]), _context()
    )
    bad = DependencyAnalyst().run(_task("nope"), _context())
    for result in (ok, bad):
        assert adapter.validate_json(adapter.dump_json(result)) == result
    assert isinstance(ok, AgentSuccess) and isinstance(bad, AgentFailure)


def test_registry_rejects_duplicates_and_unknown_ids() -> None:
    registry = AgentRegistry()
    registry.register(DependencyAnalyst())
    with pytest.raises(AgentRegistryError, match="duplicate"):
        registry.register(DependencyAnalyst())
    with pytest.raises(AgentRegistryError, match="unknown"):
        registry.get(HOSTILE)


def test_registry_finds_agents_by_declared_capability() -> None:
    registry = default_registry()
    assert [a.id for a in registry.find("dependencies_of")] == ["dependency_analyst"]
    assert [a.id for a in registry.find("explain_file")] == ["code_explainer"]
    assert registry.find("nothing") == []
