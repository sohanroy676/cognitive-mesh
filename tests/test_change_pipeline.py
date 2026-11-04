"""From a request to a checked change on a new branch (ADR-0042), with a scripted model."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.agents.registry import AgentRegistry, default_registry
from cognitive_mesh.change.checks import run_check
from cognitive_mesh.change.diffparse import Limits
from cognitive_mesh.change.models import ChangeStatus, EventKind, derive_status
from cognitive_mesh.change.pipeline import ChangeRequest, ChangeResult, make_change
from cognitive_mesh.experience.outcomes import OutcomeStore
from cognitive_mesh.providers import ModelRequest, ModelResponse
from cognitive_mesh.providers.recording import CallLog, RecordingClient
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.workspace.access import WorkspaceAccess
from cognitive_mesh.workspace.models import CheckCommand
from impact_support import Workspace, git, make_workspace

NL = chr(10)
PY = sys.executable


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


RENAME_USERS = NL.join(
    [
        "--- a/app.py",
        "+++ b/app.py",
        "@@ -4,4 +4,4 @@",
        " ",
        " ",
        '-@app.get("/users")',
        '+@app.get("/people")',
        " def users(): ...",
        "",
    ]
)


class Script:
    """Replies one after another; remembers what it was asked."""

    provider, model = "fake", "m"

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ModelResponse(provider="fake", model="m", text=text)

    def prompts(self) -> list[dict[str, object]]:
        return [json.loads(r.prompt) for r in self.requests]


def says(diff: str, summary: str = "does the thing") -> dict[str, str]:
    return {"diff": diff, "summary": summary, "reasoning": "because the request said so"}


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.ws: Workspace = make_workspace(tmp, git_projects=("api", "web"))
        graph, reads = self.ws.load()
        self.access = WorkspaceAccess("api", graph, reads)
        self.db = tmp / "t.sqlite3"
        self.outcomes = tmp / "o.sqlite3"

    def run(
        self,
        script: Script,
        *,
        check: list[str] | None = None,
        allow: bool = True,
        limits: Limits | None = None,
        files: tuple[str, ...] = ("app.py",),
    ) -> ChangeResult:
        def factory(calls: CallLog) -> AgentRegistry:
            return default_registry(RecordingClient(script, calls, None))

        declared = (
            CheckCommand(
                id=1,
                project="api",
                argv=tuple(check),
                timeout_s=60,
                by="me",
                created_at=datetime(2026, 11, 4, tzinfo=UTC),
            )
            if check
            else None
        )
        with TraceStore.create(self.db) as store:
            return make_change(
                ChangeRequest(
                    request="make a change",
                    project="api",
                    files=files,
                    limits=limits or Limits(),
                    worktrees=self.tmp / "worktrees",
                    allow_unisolated_checks=allow,
                ),
                access=self.access,
                check=declared,
                registry_factory=factory,
                store=store,
                outcomes=self.outcomes,
                runner=run_check,
            )

    def events(self) -> list[EventKind]:
        with TraceStore.open_existing(self.db) as store:
            ((_, events),) = store.read_changes()
        return [e.kind for e in events]


MARKER_CHECK = [
    PY,
    "-c",
    "import pathlib, sys; sys.exit(0 if 'FIXED' in pathlib.Path('app.py').read_text() else 1)",
]


def test_a_checked_change_lands_on_a_new_branch_and_nothing_else_moves(tmp_path: Path) -> None:
    env = Env(tmp_path)
    root = env.ws.roots["api"]
    head = git(root, "rev-parse", "HEAD")
    before = (root / "app.py").read_text(encoding="utf-8")
    result = env.run(Script(says(top_comment("FIXED"))), check=MARKER_CHECK)
    assert result.status is ChangeStatus.CHECK_PASSED
    (draft,) = result.drafts
    assert draft.outcome == "applied" and draft.commit and draft.outcome_id == 1
    assert git(root, "rev-parse", "HEAD") == head  # the checked-out branch did not move
    assert (root / "app.py").read_text(encoding="utf-8") == before  # nor the working tree
    assert git(root, "rev-parse", f"refs/heads/{result.change.branch}") == draft.commit
    # trace: the run, the model call and a model-written decision; the check is an outcome
    with TraceStore.open_existing(env.db) as store:
        decisions = store.read_decisions().decisions
        assert [d.made_by.value for d in decisions] == ["model"]
        assert decisions[0].decision_id == draft.decision_id
        assert store.get(draft.run_id or "").status.value == "succeeded"
        assert len(store.call_rows().rows) == 1
    with OutcomeStore.open_existing(env.outcomes) as outcomes:
        (row,) = outcomes.list_outcomes()
    assert (row.claim_id, row.source.value, row.result.value) == (
        draft.decision_id,
        "check",
        "succeeded",
    )
    assert "isolation env_only" in row.detail
    assert env.events() == [EventKind.IMPACT, EventKind.APPLIED, EventKind.CHECK]


def test_a_failing_check_is_recorded_then_a_revision_passes_and_both_are_kept(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    script = Script(says(top_comment("first try")), says(top_comment("FIXED")))
    result = env.run(script, check=MARKER_CHECK)
    assert [d.outcome for d in result.drafts] == ["applied", "applied"]
    assert result.status is ChangeStatus.CHECK_PASSED
    first, second = result.drafts
    assert first.commit != second.commit
    asked = script.prompts()
    assert "previous_rejection" not in {k for k, v in asked[0].items() if v}
    assert "check command failed" in str(asked[1]["previous_rejection"])
    with OutcomeStore.open_existing(env.outcomes) as outcomes:
        assert [o.result.value for o in outcomes.list_outcomes()] == ["failed", "succeeded"]


def test_every_draft_failing_leaves_the_last_failed_check_visible(tmp_path: Path) -> None:
    env = Env(tmp_path)
    result = env.run(Script(says(top_comment("never"))), check=MARKER_CHECK)
    assert len(result.drafts) == 3 and result.status is ChangeStatus.CHECK_FAILED
    with OutcomeStore.open_existing(env.outcomes) as outcomes:
        assert [o.result.value for o in outcomes.list_outcomes()] == ["failed"] * 3


def test_without_the_opt_in_or_a_command_nothing_runs_and_nothing_validates(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    result = env.run(Script(says(top_comment("x"))), check=MARKER_CHECK, allow=False)
    assert result.status is ChangeStatus.UNCHECKED
    assert not env.outcomes.exists()
    assert EventKind.CHECK_SKIPPED in env.events()
    env2 = Env(tmp_path / "second")
    result2 = env2.run(Script(says(top_comment("x"))), check=None)
    assert result2.status is ChangeStatus.UNCHECKED and not env2.outcomes.exists()


def test_a_path_into_another_project_is_refused_and_the_revision_is_fed_the_reason(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    bad = top_comment("x").replace("app.py", "../web/pyproject.toml")
    script = Script(says(bad), says(top_comment("ok")))
    result = env.run(script)
    assert [d.outcome for d in result.drafts] == ["refused:bad_path", "applied"]
    assert "bad_path" in str(script.prompts()[1]["previous_rejection"])
    assert not (env.ws.roots["web"] / "x").exists()
    assert git(env.ws.roots["web"], "status", "--porcelain") == ""


def test_a_diff_over_the_limit_is_refused(tmp_path: Path) -> None:
    env = Env(tmp_path)
    two = (
        top_comment("x")
        .replace("@@ -1,3 +1,4 @@", "@@ -1,3 +1,5 @@")
        .replace("+# x", "+# x" + NL + "+# y")
    )
    result = env.run(Script(says(two)), limits=Limits(max_files=1, max_lines=1))
    assert [d.outcome for d in result.drafts] == ["refused:too_many_lines"] * 3
    assert result.status is ChangeStatus.NO_DRAFT
    root = env.ws.roots["api"]
    assert git(root, "rev-parse", result.change.branch) == result.change.base_commit


def test_a_malformed_reply_or_prose_fails_explicitly_and_stops(tmp_path: Path) -> None:
    env = Env(tmp_path)
    result = env.run(Script("I would love to help with that!"))
    assert result.status is ChangeStatus.NO_DRAFT
    assert [d.outcome for d in result.drafts] == ["coder_failed:malformed_output"]
    prose = env.run(Script(says("Here is a diff: nothing really")))
    assert prose.drafts[0].outcome == "refused:not_a_unified_diff"


def test_renaming_a_route_a_dependent_calls_is_refused_by_the_impact_check(tmp_path: Path) -> None:
    env = Env(tmp_path)
    script = Script(says(RENAME_USERS), says(top_comment("fine")))
    result = env.run(script)
    assert [d.outcome for d in result.drafts] == ["refused:impact_breaking", "applied"]
    feedback = str(script.prompts()[1]["previous_rejection"])
    assert "breaks contracts" in feedback and "/users" in feedback
    assert derive_status_of(env) is ChangeStatus.UNCHECKED


def derive_status_of(env: Env) -> ChangeStatus:
    with TraceStore.open_existing(env.db) as store:
        ((_, events),) = store.read_changes()
    return derive_status(events)


def test_a_current_project_that_is_not_the_named_one_cannot_be_written(tmp_path: Path) -> None:
    env = Env(tmp_path)
    graph, reads = env.ws.load()
    env.access = WorkspaceAccess("web", graph, reads)  # current is web, request names api
    with pytest.raises(Exception, match="current project"):
        env.run(Script(says(top_comment("x"))))
