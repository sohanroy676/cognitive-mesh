"""Helpers for the ADR-0043 tests: a scripted model, a two-task set on the impact workspace."""

import json
import sys
from pathlib import Path

from cognitive_mesh.agents.registry import AgentRegistry, default_registry
from cognitive_mesh.evaluate.runner import EvalSettings
from cognitive_mesh.evaluate.taskset import TaskSet, load_taskset
from cognitive_mesh.providers import ModelRequest, ModelResponse
from cognitive_mesh.providers.recording import CallLog, RecordingClient
from impact_support import Workspace, make_workspace

NL = chr(10)
PY = sys.executable
PASS_CHECK = [PY, "-c", "import sys; sys.exit(0)"]
FAIL_CHECK = [PY, "-c", "import sys; sys.exit(1)"]


def top_comment(text: str) -> str:
    return NL.join(
        [
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,3 +1,4 @@",
            f"+# {text}",
            " from fastapi import FastAPI",
            " ",
            " app = FastAPI()",
            "",
        ]
    )


class Script:
    """Replies with the same diff every time and remembers every prompt it was sent."""

    provider, model = "fake", "m"

    def __init__(self, diff: str | None = None, summary: str = "adds a note") -> None:
        self.diff = diff or top_comment("note")
        self.summary = summary
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        reply = {"diff": self.diff, "summary": self.summary, "reasoning": "asked to"}
        return ModelResponse(provider="fake", model="m", text=json.dumps(reply))

    def prompts(self) -> list[dict[str, object]]:
        return [json.loads(r.prompt) for r in self.requests]


def factory_for(script: Script, entry: str | None = "m1"):  # type: ignore[no-untyped-def]
    def make(calls: CallLog) -> AgentRegistry:
        return default_registry(RecordingClient(script, calls, entry))

    return make


def write_taskset(
    tmp: Path, ws: Workspace, tasks: list[tuple[str, str, list[str]]], name: str = "demo"
) -> Path:
    lines = [
        "[set]",
        f'name = "{name}"',
        f'workspace = "{ws.file.as_posix()}"',
        'project = "api"',
        f'root = "{ws.roots["api"].as_posix()}"',
        "",
    ]
    for task_id, request, check in tasks:
        words = ", ".join(json.dumps(w) for w in check)
        lines += [
            "[[task]]",
            f'id = "{task_id}"',
            f'request = "{request}"',
            'files = ["app.py"]',
            f"check = [{words}]",
            "check_timeout_s = 60",
            "",
        ]
    path = tmp / "tasks.toml"
    path.write_text(NL.join(lines), encoding="utf-8", newline="\n")
    return path


def make_set(
    tmp: Path, tasks: list[tuple[str, str, list[str]]] | None = None
) -> tuple[Workspace, TaskSet]:
    ws = make_workspace(tmp / "w", git_projects=("api", "web"))
    chosen = tasks or [
        ("t1", "add a note comment to the app module", PASS_CHECK),
        ("t2", "add a note comment to the app module again", PASS_CHECK),
    ]
    return ws, load_taskset(write_taskset(tmp, ws, chosen))


def settings(
    out: Path, *, repeats: int = 1, checks: bool = True, entry: str = "m1"
) -> EvalSettings:
    return EvalSettings(
        out=out,
        repeats=repeats,
        model_entry=entry,
        catalog_sha256=None,
        allow_unisolated_checks=checks,
    )
