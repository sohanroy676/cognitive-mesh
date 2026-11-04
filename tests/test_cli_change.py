"""The `change` commands end to end through `main()` (ADR-0042): scripted model, real git."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from cognitive_mesh import cli
from cognitive_mesh.agents.registry import AgentRegistry, default_registry
from cognitive_mesh.change import cli as change_cli
from cognitive_mesh.change.github import PublishError, PublishFailure, PullRequest
from cognitive_mesh.cli import main
from cognitive_mesh.providers.recording import CallLog, RecordingClient
from impact_support import Workspace, git, make_workspace
from test_change_pipeline import MARKER_CHECK, Script, says, top_comment

FAKE = "tok" + "en-value-for-tests-0123456789"


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.ws: Workspace = make_workspace(tmp, git_projects=("api", "web"))
        self.root = self.ws.roots["api"]
        self.db = tmp / "t.sqlite3"
        self.outcomes = tmp / "o.sqlite3"
        self.worktrees = tmp / "worktrees"
        git(self.root, "remote", "add", "origin", "https://github.com/octo/demo.git")

    def use(self, monkeypatch: pytest.MonkeyPatch, script: Script) -> None:
        def factory(_args: object) -> Callable[[CallLog], AgentRegistry]:
            return lambda calls: default_registry(RecordingClient(script, calls, None))

        monkeypatch.setattr(change_cli, "_registry_factory", factory)

    def set_check(self) -> None:
        argv = ["workspace", "set-check", "--workspace", str(self.ws.file), "--by", "me", "api"]
        assert main([*argv, "--", *MARKER_CHECK]) == 0

    def propose(self, *extra: str) -> int:
        return main(
            [
                "change", "propose", "make a change", "--root", str(self.root),
                "--workspace", str(self.ws.file), "--project", "api", "--db", str(self.db),
                "--outcomes", str(self.outcomes), "--worktrees", str(self.worktrees),
                "--file", "app.py", *extra,
            ]
        )  # fmt: skip

    def change_id(self) -> str:
        self.captured.readouterr()  # drop what the previous command printed
        out = json.loads(self.run_json("change", "list", "--db", str(self.db), "--json"))
        return str(out[0]["change_id"])

    def run_json(self, *argv: str) -> str:
        self.captured.readouterr()
        assert main(list(argv)) == 0
        return self.captured.readouterr().out

    captured: pytest.CaptureFixture[str]


@pytest.fixture
def world(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> World:
    w = World(tmp_path)
    w.captured = capsys
    return w


def test_the_exit_codes_match_cli_py() -> None:
    assert cli.EXIT_CHANGE_ERROR == change_cli.EXIT_CHANGE_ERROR == 24
    assert change_cli.EXIT_TRACE_STORE_ERROR == cli.EXIT_TRACE_STORE_ERROR
    assert change_cli.EXIT_WORKSPACE_ERROR == cli.EXIT_WORKSPACE_ERROR
    assert change_cli.EXIT_TASK_NOT_SUCCEEDED == cli.EXIT_TASK_NOT_SUCCEEDED
    assert change_cli.EXIT_CONFIG_ERROR == cli.EXIT_CONFIG_ERROR


def test_propose_without_the_opt_in_does_not_run_the_check_and_says_so(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("FIXED"))))
    assert world.propose() == 0
    out = world.captured.readouterr().out
    assert "unchecked" in out and "--allow-unisolated-checks" in out
    assert "nothing is pushed or merged" in out and "not verified" in out
    assert not world.outcomes.exists()


def test_a_full_pass_from_propose_to_a_pull_request(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("FIXED"), "adds a marker")))
    assert world.propose("--allow-unisolated-checks") == 0
    out = world.captured.readouterr().out
    assert "check_passed" in out
    cid = world.change_id()
    # no approval yet: the pull request step is refused and nothing is pushed
    pushed: list[str] = []
    monkeypatch.setattr(change_cli, "push_branch", lambda *a, **k: pushed.append("push"))
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    args = ["change", "pr", cid, "--db", str(world.db), "--root", str(world.root)]
    assert main(args) == 24 and pushed == []
    assert "no approval" in world.captured.readouterr().err
    assert main(["change", "approve", cid, "--db", str(world.db), "--root", str(world.root),
                 "--by", "me", "--note", "read it"]) == 0  # fmt: skip
    opened: list[dict[str, object]] = []

    def fake_pr(owner: str, repo: str, head: str, base: str, title: str, body: str,
                token: SecretStr | None, **_k: object) -> PullRequest:  # fmt: skip
        opened.append({"owner": owner, "repo": repo, "head": head, "base": base,
                       "title": title, "body": body, "token": token})  # fmt: skip
        return PullRequest("https://github.com/octo/demo/pull/9", 9)

    monkeypatch.setattr(change_cli, "create_pull_request", fake_pr)
    world.captured.readouterr()
    assert main(args) == 0
    seen = world.captured.readouterr()
    assert "pull request opened: https://github.com/octo/demo/pull/9" in seen.out
    assert FAKE not in seen.out + seen.err
    (call,) = opened
    assert (call["owner"], call["repo"], call["base"]) == ("octo", "demo", "master")
    body = str(call["body"])
    assert "unverified" in body and "not isolated" in body and "adds a marker" in body
    assert FAKE not in body and "nothing here was merged" in body.lower()
    assert pushed == ["push"]
    # a second run reports the open pull request instead of pushing again
    assert main(args) == 0 and pushed == ["push"]
    shown = json.loads(world.run_json("change", "show", cid, "--db", str(world.db), "--json"))
    kinds = [e["kind"] for e in shown["events"]]
    assert kinds[-3:] == ["approved", "pushed", "pr_opened"] and shown["status"] == "pr_opened"


def test_a_stale_approval_or_a_moved_branch_stops_the_pull_request(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("FIXED"))))
    assert world.propose("--allow-unisolated-checks") == 0
    cid = world.change_id()
    common = ["--db", str(world.db), "--root", str(world.root)]
    assert main(["change", "approve", cid, *common, "--by", "me"]) == 0
    wt = next(world.worktrees.iterdir())
    (wt / "app.py").write_text("# sneaky\n", encoding="utf-8")
    git(wt, "commit", "-aqm", "sneaky")  # the branch moves after the approval
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    monkeypatch.setattr(change_cli, "push_branch", lambda *a, **k: pytest.fail("pushed"))
    world.captured.readouterr()
    assert main(["change", "pr", cid, *common]) == 24
    assert "no longer points" in world.captured.readouterr().err
    assert main(["change", "approve", cid, *common, "--by", "me"]) == 24


def test_a_failing_check_blocks_the_pull_request_unless_overridden_and_labelled(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("never"))))
    assert world.propose("--allow-unisolated-checks") == 7  # not succeeded
    assert "check_failed" in world.captured.readouterr().out
    cid = world.change_id()
    common = ["--db", str(world.db), "--root", str(world.root)]
    assert main(["change", "approve", cid, *common, "--by", "me"]) == 0
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    monkeypatch.setattr(change_cli, "push_branch", lambda *a, **k: None)
    bodies: list[str] = []

    def fake_pr(*a: object, **k: object) -> PullRequest:
        bodies.append(str(a[5]))
        return PullRequest("https://github.com/octo/demo/pull/1", 1)

    monkeypatch.setattr(change_cli, "create_pull_request", fake_pr)
    world.captured.readouterr()
    assert main(["change", "pr", cid, *common]) == 24
    assert "did not pass" in world.captured.readouterr().err and bodies == []
    assert main(["change", "pr", cid, *common, "--without-passing-check"]) == 0
    assert "without a passing check" in bodies[0]


def test_no_token_or_no_remote_opt_in_publishes_nothing_and_a_failure_is_recorded(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("FIXED"))))
    assert world.propose("--allow-unisolated-checks") == 0
    cid = world.change_id()
    common = ["--db", str(world.db), "--root", str(world.root)]
    assert main(["change", "approve", cid, *common, "--by", "me"]) == 0
    world.captured.readouterr()
    assert main(["change", "pr", cid, *common]) == 24  # no opt-in
    assert "COGMESH_ALLOW_REMOTE_CONTENT" in world.captured.readouterr().err
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    assert main(["change", "pr", cid, *common]) == 24  # no token
    assert "GITHUB_TOKEN" in world.captured.readouterr().err
    shown = json.loads(world.run_json("change", "show", cid, "--db", str(world.db), "--json"))
    assert shown["events"][-1]["kind"] == "push_failed"
    assert shown["events"][-1]["detail"] == PublishFailure.NOT_CONFIGURED.value
    assert shown["status"] == "approved"  # the branch stays local and the change is still usable
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)

    def refuse(*a: object, **k: object) -> None:
        raise PublishError(PublishFailure.REJECTED)

    monkeypatch.setattr(change_cli, "push_branch", refuse)
    assert main(["change", "pr", cid, *common]) == 24
    assert "rejected the push" in world.captured.readouterr().err


def test_a_failed_pull_request_keeps_the_pushed_branch_and_can_be_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.set_check()
    world.use(monkeypatch, Script(says(top_comment("FIXED"))))
    assert world.propose("--allow-unisolated-checks") == 0
    cid = world.change_id()
    common = ["--db", str(world.db), "--root", str(world.root)]
    assert main(["change", "approve", cid, *common, "--by", "me"]) == 0
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    pushes: list[int] = []
    monkeypatch.setattr(change_cli, "push_branch", lambda *a, **k: pushes.append(1))

    def broken(*a: object, **k: object) -> PullRequest:
        raise PublishError(PublishFailure.UNPROCESSABLE)

    monkeypatch.setattr(change_cli, "create_pull_request", broken)
    world.captured.readouterr()
    assert main(["change", "pr", cid, *common]) == 24
    captured = world.captured.readouterr()
    assert "branch is pushed" in captured.out and "refused the pull request" in captured.err
    monkeypatch.setattr(
        change_cli, "create_pull_request",
        lambda *a, **k: PullRequest("https://github.com/octo/demo/pull/2", 2),
    )  # fmt: skip
    assert main(["change", "pr", cid, *common]) == 0
    assert pushes == [1]  # the second run did not push again


def test_list_show_and_discard_keep_the_branch(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.use(monkeypatch, Script(says(top_comment("x"))))
    assert world.propose() == 0
    cid = world.change_id()
    assert main(["change", "list", "--db", str(world.db)]) == 0
    assert main(["change", "show", cid, "--db", str(world.db)]) == 0
    out = world.captured.readouterr().out
    assert cid in out and "unchecked" in out
    common = ["--db", str(world.db), "--root", str(world.root)]
    assert main(["change", "discard", cid, *common]) == 0
    assert not any(world.worktrees.iterdir())
    branch = f"cogmesh/change-{cid}"
    assert git(world.root, "rev-parse", "--verify", branch)
    assert main(["change", "discard", cid, *common]) == 24
    assert main(["change", "show", "nope", "--db", str(world.db)]) == 24
    assert main(["change", "list", "--db", str(world.tmp / "missing.sqlite3")]) == 8


def test_bad_arguments_exit_cleanly(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.use(monkeypatch, Script(says(top_comment("x"))))
    assert world.propose("--max-lines", "9999") == 24
    assert world.propose("--worktrees", str(world.root / "inside")) == 24
    assert "outside the project" in world.captured.readouterr().err
