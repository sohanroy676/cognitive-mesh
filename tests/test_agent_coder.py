"""The coder role (ADR-0042): current project only, output unverified, plans refuse it."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.agents import AgentContext, AgentFailure, AgentSuccess, AgentTask, FailureKind
from cognitive_mesh.agents.coder import Coder
from cognitive_mesh.agents.registry import default_registry
from cognitive_mesh.orchestrator.models import PlanError, TaskPlan
from cognitive_mesh.orchestrator.planner import plan_task
from cognitive_mesh.providers import ModelRequest, ModelResponse
from cognitive_mesh.workspace.access import WorkspaceAccess
from impact_support import make_workspace

NL = chr(10)
DIFF = "--- a/app.py" + NL + "+++ b/app.py" + NL + "@@ -1 +1 @@" + NL + "-a" + NL + "+b" + NL


class _Says:
    provider, model = "fake", "m"

    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return ModelResponse(provider="fake", model="m", text=text)


GOOD = {"diff": DIFF, "summary": "s", "reasoning": "r"}


def _task(**inputs: object) -> AgentTask:
    base: dict[str, object] = {"project": "api", "request": "make it better"}
    return AgentTask(task_id="t", capability="propose_change", inputs={**base, **inputs})


def _context(tmp: Path) -> AgentContext:
    graph, reads = make_workspace(tmp).load()
    return AgentContext(workspace=WorkspaceAccess("api", graph, reads))


def test_files_go_to_the_model_as_data_and_the_reply_is_unverified(tmp_path: Path) -> None:
    client = _Says(GOOD)
    result = Coder(client).run(
        _task(files=["app.py"], previous_rejection="too long"), _context(tmp_path)
    )
    assert isinstance(result, AgentSuccess)
    assert result.basis.value == "model_unverified" and result.model is not None
    assert result.output["diff"] == DIFF
    sent = json.loads(client.requests[0].prompt)
    assert [f["path"] for f in sent["files"]] == ["app.py"]
    assert "FastAPI" in sent["files"][0]["content"]
    assert sent["previous_rejection"] == "too long"
    assert [(e.path, len(e.sha256)) for e in result.evidence] == [("api:app.py", 64)]
    assert "untrusted" in client.requests[0].system


def test_another_project_cannot_be_written(tmp_path: Path) -> None:
    client = _Says(GOOD)
    result = Coder(client).run(_task(project="web"), _context(tmp_path))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.INVALID_INPUT
    assert "web" not in result.message
    assert client.requests == []  # the model was never asked


def test_no_workspace_means_no_change(tmp_path: Path) -> None:
    result = Coder(_Says(GOOD)).run(_task(), AgentContext())
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.MISSING_CONTEXT


@pytest.mark.parametrize("path", ["nope.py", ".env.example2", "../web/pyproject.toml"])
def test_an_unreadable_file_is_refused_before_the_model_is_asked(tmp_path: Path, path: str) -> None:
    client = _Says(GOOD)
    result = Coder(client).run(_task(files=[path]), _context(tmp_path))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.INVALID_INPUT
    assert path not in result.message and client.requests == []


def test_too_many_files_is_an_input_error(tmp_path: Path) -> None:
    result = Coder(_Says(GOOD)).run(_task(files=["a", "b", "c", "d"]), _context(tmp_path))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.INVALID_INPUT


@pytest.mark.parametrize(
    "reply",
    ["not json", {"diff": "", "summary": "s", "reasoning": "r"}, {**GOOD, "extra": 1}, {"diff": 3}],
)
def test_a_reply_off_the_contract_fails_explicitly(tmp_path: Path, reply: object) -> None:
    result = Coder(_Says(reply)).run(_task(), _context(tmp_path))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.MALFORMED_OUTPUT


def test_plans_refuse_a_step_that_writes_unless_the_caller_allows_it() -> None:
    registry = default_registry()
    plan = TaskPlan.model_validate(
        {
            "task_id": "t",
            "steps": [
                {
                    "step_id": "c",
                    "capability": "propose_change",
                    "inputs": {"project": "api", "request": "x"},
                }
            ],
        }
    )
    with pytest.raises(PlanError, match="would change a project"):
        plan_task(plan, registry)
    assert [s.step_id for s in plan_task(plan, registry, allow_writes=True).order] == ["c"]
    assert registry.writes("propose_change") and not registry.writes("dependencies_of")
