"""The whole chat flow with scripted models (ADR-0044): onboarding, plan, change, approvals."""

import threading
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.providers import ModelRequest, ModelResponse
from cognitive_mesh.serve.cli import Servers
from conductor_support import EXPLAIN_OK, ScriptedClient, reply
from impact_support import make_workspace
from serve_support import TOKEN, Models, call, config_for, running, wait_for


def post(s: Servers, path: str, body: dict[str, Any]) -> Any:
    return call(s.app, "POST", path, token=TOKEN, body=body)


def get(server: Any, path: str) -> Any:
    return call(server, "GET", path, token=TOKEN)


def onboard(s: Servers, tmp: Path) -> int:
    ws = make_workspace(tmp / "w", git_projects=("api", "web"))
    for name in ("api", "web"):
        r = post(s, "/api/projects/add", {"source": str(ws.roots[name]), "name": name})
        assert r.status == 200, r.raw
    session = post(s, "/api/projects/confirm", {"project": "api"})
    assert session.status == 200
    return int(session.json["session"])


def send(s: Servers, session: int, text: str, memory: bool = True, mode: str = "change") -> int:
    body = {"session": session, "text": text, "memory": memory, "mode": mode}
    r = post(s, "/api/chat/send", body)
    assert r.status == 200, r.raw
    return int(r.json["turn"])


def status_of(s: Servers, turn: int) -> str:
    return str(get(s.app, f"/api/turn?id={turn}").json["turn"]["status"])


def why(s: Servers, turn: int) -> str:
    return str(get(s.app, f"/api/turn?id={turn}").json["events"])


def request_of(s: Servers, turn: int, action: str) -> dict[str, Any]:
    events = get(s.app, f"/api/turn?id={turn}").json["events"]
    found = [
        e["data"]
        for e in events
        if e["kind"] == "approval_request" and e["data"]["action"] == action
    ]
    assert found, [e["kind"] for e in events]
    return dict(found[-1])


def body_for(req: dict[str, Any], **extra: Any) -> dict[str, Any]:
    keys = ("approval_id", "change_id", "diff_sha256")
    return {**{k: req[k] for k in keys}, **extra}


def test_onboarding_shows_detected_and_declared_edges_apart(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        onboard(s, tmp_path)
        graph = get(s.app, "/api/state").json["graph"]
        detected = [e for e in graph["edges"] if not e["declared"]]
        assert any(e["source"] == "web" and e["target"] == "api" for e in detected)
        r = post(
            s,
            "/api/projects/link",
            {"source": "api", "target": "web", "kind": "notifies", "note": "webhook"},
        )
        assert r.status == 200
        edges = get(s.graph, "/api/workspace").json["edges"]
        declared = [e for e in edges if e["declared"]]
        assert [(e["source"], e["target"], e["kind"]) for e in declared] == [
            ("api", "web", "notifies")
        ]
        assert "not detected evidence" in declared[0]["origin"]
        assert all(not e["declared"] for e in edges if e not in declared)


def test_a_change_needs_an_explicit_approval_and_replays_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = Models()
    opened: list[str] = []

    def fake_pr(*args: Any, **kw: Any) -> tuple[str, bool]:
        opened.append("pr")
        return "https://github.com/o/r/pull/1", False

    monkeypatch.setattr("cognitive_mesh.serve.app_api.open_pull_request", fake_pr)
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "awaiting_approval"), why(s, turn)

        events = get(s.app, f"/api/turn?id={turn}").json["events"]
        plan = next(e["data"] for e in events if e["kind"] == "plan")
        assert plan["assignments"][0]["model"] == "worker"
        assert plan["assignments"][0]["reason"] == "a small edit to one file"
        assert any(e["kind"] == "analysis" for e in events)
        coder = next(e["data"] for e in events if e["kind"] == "coder")
        assert coder["model"] == "worker" and coder["files"] == ["app.py"]
        assert plan["basis"] == "model_unverified"
        assert any(e["kind"] == "draft" and e["data"]["outcome"] == "applied" for e in events)
        req = request_of(s, turn, "approve")
        assert "+# note" in req["diff"] and req["basis"] == "model_unverified"
        assert opened == []  # nothing was pushed or opened

        # a wrong diff hash, an invented id and a wrong action are refused and spend nothing
        bad = post(s, "/api/change/approve", body_for(req, diff_sha256="0" * 64))
        assert bad.status == 409 and "different action or diff" in bad.json["error"]
        assert post(s, "/api/change/approve", body_for(req, approval_id="f" * 32)).status == 409
        assert post(s, "/api/change/pr", body_for(req)).status == 409  # an approve request

        ok = post(s, "/api/change/approve", body_for(req, note="read it"))
        assert ok.status == 200, ok.raw
        replay = post(s, "/api/change/approve", body_for(req))
        assert replay.status == 409 and "already used" in replay.json["error"]
        assert opened == []  # an approval is not a push

        pr = request_of(s, turn, "pr")
        assert pr["approval_id"] != req["approval_id"]
        done = post(s, "/api/change/pr", body_for(pr))
        assert done.status == 200 and done.json["pr_url"].startswith("https://github.com/")
        assert opened == ["pr"]
        assert post(s, "/api/change/pr", body_for(pr)).status == 409
        assert status_of(s, turn) == "done"

        trace = get(s.app, f"/api/trace?turn={turn}").json
        who = {d["made_by"] for d in trace["decisions"]}
        assert who == {"model", "person"}  # the coder's draft and the person's approval
        assert all(d["basis"] for d in trace["decisions"])
        assert trace["changes"][0]["status"] == "approved"
        assert {c["entry_id"] for c in trace["calls"]} == {"cond", "worker"}  # plan and draft


def test_reject_records_a_decision_and_closes_the_turn(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "awaiting_approval")
        req = request_of(s, turn, "approve")
        assert post(s, "/api/change/reject", body_for(req, note="not wanted")).status == 200
        assert status_of(s, turn) == "rejected"
        assert post(s, "/api/change/approve", body_for(req)).status == 409
        trace = get(s.app, f"/api/trace?turn={turn}").json
        assert any(d["chosen"] == "do not approve" for d in trace["decisions"])


def test_revise_starts_a_new_turn_and_needs_a_note(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "awaiting_approval")
        req = request_of(s, turn, "approve")
        assert post(s, "/api/change/revise", body_for(req)).status == 400
        r = post(s, "/api/change/revise", body_for(req, note="shorter"))
        assert r.status == 200 and r.json["turn"] != turn
        assert wait_for(lambda: status_of(s, r.json["turn"]) == "awaiting_approval")
        assert status_of(s, turn) == "rejected"


def test_memory_from_the_first_turn_is_offered_to_the_second(tmp_path: Path) -> None:
    models = Models()
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        first = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, first) == "awaiting_approval")
        assert wait_for(lambda: not s.chat.running_turn())
        second = send(s, session, "add a note comment to the app module again", memory=True)
        assert wait_for(lambda: status_of(s, second) == "awaiting_approval")
        events = get(s.app, f"/api/turn?id={second}").json["events"]
        memory = next(e["data"] for e in events if e["kind"] == "memory")
        assert memory["used"] is True
        third_off = None
        assert wait_for(lambda: not s.chat.running_turn())
        third_off = send(s, session, "add a note comment to the app module a third time", False)
        assert wait_for(lambda: status_of(s, third_off) == "awaiting_approval")
        off = next(
            e["data"]
            for e in get(s.app, f"/api/turn?id={third_off}").json["events"]
            if e["kind"] == "memory"
        )
        assert off["used"] is False and off["past_changes"] == 0


def test_a_question_is_answered_without_a_write(tmp_path: Path) -> None:
    steps = [
        {
            "step_id": "explain",
            "capability": "explain_file",
            "inputs": {"path": "app.py"},
        }
    ]
    assign = {"explain": {"model": "worker", "reason": "a small file"}}
    models = Models(reply(steps, assign, "just read one file"))
    models.worker = ScriptedClient(EXPLAIN_OK, repeat=True)
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "what does app.py do", mode="ask")
        assert wait_for(lambda: status_of(s, turn) == "done")
        events = get(s.app, f"/api/turn?id={turn}").json["events"]
        answer = next(e["data"] for e in events if e["kind"] == "analysis")
        assert answer["status"] == "succeeded"
        assert answer["steps"][0]["basis"] == "model_unverified"
        assert not [e for e in events if e["kind"] == "approval_request"]
        trace = get(s.app, f"/api/trace?turn={turn}").json
        assert trace["decisions"]  # the conductor's plan and assignment are decision records


def test_a_plan_the_conductor_cannot_make_fails_the_turn(tmp_path: Path) -> None:
    models = Models("this is not a plan")
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "failed")
        events = get(s.app, f"/api/turn?id={turn}").json["events"]
        assert "no usable plan" in events[-1]["data"]["message"]


def test_without_a_model_the_chat_refuses_to_start_a_turn(tmp_path: Path) -> None:
    with running(config_for(tmp_path, catalog=None, conductor=None)) as s:
        session = onboard(s, tmp_path)
        r = post(s, "/api/chat/send", {"session": session, "text": "hello", "memory": True})
        assert r.status == 409 and "no model is configured" in r.json["error"]


def test_only_one_task_runs_at_a_time(tmp_path: Path) -> None:
    release = threading.Event()
    entered = threading.Event()

    class Slow:
        provider, model = "fake", "slow"

        def complete(self, request: ModelRequest) -> ModelResponse:
            entered.set()
            release.wait(30)
            return ModelResponse(provider="fake", model="slow", text="not a plan")

    models = Models()
    models.conductor = Slow()
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        first = send(s, session, "add a note comment to the app module")
        assert entered.wait(30)
        second = post(s, "/api/chat/send", {"session": session, "text": "again", "memory": True})
        assert second.status == 409 and f"turn {first}" in second.json["error"]
        release.set()
        assert wait_for(lambda: status_of(s, first) == "failed")


def test_a_restart_leaves_an_unfinished_turn_interrupted_and_old_approvals_dead(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    with running(config) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "awaiting_approval")
        assert wait_for(lambda: not s.chat.running_turn())
        req = request_of(s, turn, "approve")
    with running(config) as again:  # the same home, a new process in effect
        assert status_of(again, turn) == "interrupted"
        late = post(again, "/api/change/approve", body_for(req))
        assert late.status == 409 and "restarted" in late.json["error"]
        assert get(again.app, f"/api/turn?id={turn}").json["approvals"] == []
