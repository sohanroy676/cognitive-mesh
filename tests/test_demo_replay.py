"""Recorded model replies make a demo that runs the real application with no model or key."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.providers import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    load_catalog,
)
from cognitive_mesh.serve.replay import (
    CaptureClient,
    ProjectSpec,
    Recording,
    ReplayError,
    ReplayModels,
    Reply,
    classify,
    load_recording,
    make_git_sample,
    run_recordings,
    write_replay_catalog,
)
from serve_support import TOKEN, call, config_for, running, wait_for
from test_serve_flow import body_for, get, onboard, post, request_of, send, status_of

DEMO = Path(__file__).parent.parent / "demo"
REAL_CATALOG = Path(__file__).parent.parent / "docs" / "serve-catalog.toml"


def request(system: str, prompt: str = "{}") -> ModelRequest:
    return ModelRequest(system=system, prompt=prompt, timeout_s=5.0, max_output_chars=1000)


def test_requests_are_told_apart_by_agent_and_file() -> None:
    assert classify(request("You are the conductor of a code-analysis tool.")) == ("conductor", "")
    assert classify(request("You propose one small change to a project")) == ("coder", "")
    assert classify(request("You explain one source file.", '{"path": "a/b.py"}')) == (
        "explainer",
        "a/b.py",
    )
    assert classify(request("You explain one source file.", "not json")) == ("explainer", "")
    assert classify(request("You answer one question about a project")) == ("answerer", "")
    assert classify(request("Something else entirely")) == ("other", "")


def recording(*replies: Reply, text: str = "do it") -> Recording:
    return Recording(
        title="t",
        project=ProjectSpec(name="p", sample="s"),
        request=text,
        mode="change",
        replies=list(replies),
    )


def test_replies_play_back_in_order_per_agent_and_file_and_errors_are_replayed() -> None:
    models = ReplayModels(
        [
            recording(
                Reply(role="explainer", key="a.py", entry="e", provider="pv", model="m", text="A1"),
                Reply(role="explainer", key="a.py", entry="e", error="rate_limited"),
                Reply(role="explainer", key="b.py", entry="e", provider="pv", model="m", text="B"),
            )
        ]
    )
    models.select("do it")
    client = models("e")
    ask = request("You explain one source file.", '{"path": "a.py"}')
    assert (client.provider, client.model) == ("pv", "m")  # the recorded model is shown
    assert client.complete(ask).text == "A1"
    with pytest.raises(ProviderError) as err:
        client.complete(ask)
    assert err.value.kind is ProviderErrorKind.RATE_LIMITED
    other = request("You explain one source file.", '{"path": "b.py"}')
    assert client.complete(other).text == "B"
    with pytest.raises(ProviderError) as gone:  # nothing recorded is left
        client.complete(other)
    assert gone.value.kind is ProviderErrorKind.UNAVAILABLE
    models.select("do it")  # selecting again starts the recording over
    assert client.complete(ask).text == "A1"


def test_a_recorded_revision_answers_the_revise_button_with_any_note() -> None:
    first = recording(text="the request")
    revision = Recording(
        title="r",
        project=ProjectSpec(name="p", sample="s"),
        request="the request (revise: use the catalog)",
        mode="change",
        revise_note="use the catalog",
        replies=[Reply(role="coder", key="", entry="e", provider="pv", model="m", text="REV")],
    )
    models = ReplayModels([first, revision])
    assert models.notes() == {"the request": "use the catalog"}  # the page pre-fills this
    for note in ("use the catalog", "my own words"):
        text = f"the request (revise: {note})"
        assert models.check(text) is None
        models.select(text)
        assert models("e").complete(request("You propose one small change")).text == "REV"
    assert "no recorded revision" in (models.check("another (revise: x)") or "")


def test_only_recorded_requests_are_accepted() -> None:
    models = ReplayModels([recording(text="the recorded request")])
    assert models.check("  the recorded request ") is None
    assert "only the recorded requests" in (models.check("something else") or "")


def test_the_replay_catalog_has_the_real_ids_and_needs_no_key(tmp_path: Path) -> None:
    written = write_replay_catalog(REAL_CATALOG, tmp_path)
    catalog = load_catalog(written)
    real = load_catalog(REAL_CATALOG)
    assert set(catalog.models) == set(real.models)
    for entry_id, entry in catalog.models.items():
        assert entry.api_key_env is None and not entry.remote
        assert entry.roles == real.models[entry_id].roles
    with pytest.raises(ReplayError):
        write_replay_catalog(tmp_path / "missing.toml", tmp_path)


def test_a_capture_notes_replies_and_errors_and_passes_them_on() -> None:
    class Inner:
        provider, model = "pv", "m"

        def __init__(self) -> None:
            self.fail = False

        def complete(self, req: ModelRequest) -> ModelResponse:
            if self.fail:
                raise ProviderError(ProviderErrorKind.TIMEOUT)
            return ModelResponse(
                provider="pv", model="m", text="hello", prompt_tokens=11, completion_tokens=3
            )

    inner = Inner()
    sink: list[dict[str, Any]] = []
    client = CaptureClient(inner, sink, "entry")
    ask = request("You propose one small change to a project")
    assert client.complete(ask).text == "hello"
    inner.fail = True
    with pytest.raises(ProviderError):
        client.complete(ask)
    assert [n.get("text") or n.get("error") for n in sink] == ["hello", "timeout"]
    assert {n["role"] for n in sink} == {"coder"} and sink[0]["entry"] == "entry"
    assert (sink[0]["prompt_tokens"], sink[0]["completion_tokens"]) == (11, 3)
    assert all(isinstance(n["latency_ms"], int) for n in sink)  # the failure took time too


@pytest.mark.parametrize("content", ["not json", "{}", json.dumps({"title": "t"})])
def test_a_broken_recording_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "r.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ReplayError):
        load_recording(path)
    with pytest.raises(ReplayError):
        load_recording(tmp_path / "missing.json")


def test_a_recorded_turn_replays_as_a_real_one_with_working_buttons(tmp_path: Path) -> None:
    # 1. a normal scripted turn, with the replies captured
    captured = tmp_path / "captured"
    live = config_for(tmp_path / "live", capture_dir=captured)
    with running(live) as s:
        onboard_session = onboard(s, tmp_path)
        turn = send(s, onboard_session, "add a note comment to the app module")
        assert wait_for(lambda: status_of(s, turn) == "awaiting_approval")
    notes = sorted(captured.glob("turn-*.json"))
    assert len(notes) == 1
    note = json.loads(notes[0].read_text(encoding="utf-8"))
    assert {r["role"] for r in note["replies"]} >= {"conductor", "explainer", "coder"}

    # 2. the same project as a sample next to a recording
    root = tmp_path / "w" / "api"  # the project `onboard` made
    base = tmp_path / "rec"
    shutil.copytree(root, base / "sample", ignore=shutil.ignore_patterns(".git"))
    rec = Recording(
        title="app note",
        project=ProjectSpec(name="api", sample="sample"),
        request=note["request"],
        mode=note["mode"],
        conductor=note["conductor"],
        replies=[
            Reply(**{**r, "latency_ms": 1234, "prompt_tokens": 55, "completion_tokens": 7})
            for r in note["replies"]
        ],
        source=base / "r.json",
    )

    # 3. a fresh home that has no models: the recording makes a real, waiting turn
    demo = ReplayModels([rec])
    home = tmp_path / "demo"
    catalog = write_replay_catalog(tmp_path / "live" / "catalog.toml", home)
    config = config_for(
        home,
        demo,  # type: ignore[arg-type]  # the factory is all the config needs
        catalog=catalog,
        conductor=note["conductor"],
        demo=demo,
    )
    with running(config) as s:
        run_recordings(s.api, [rec], config.home)
        sessions = get(s.app, "/api/sessions").json["sessions"]
        assert [x["project"] for x in sessions] == ["api"]
        turn = get(s.app, f"/api/session?id={sessions[0]['id']}").json["turns"][0]["id"]
        assert status_of(s, turn) == "awaiting_approval"
        events = get(s.app, f"/api/turn?id={turn}").json["events"]
        kinds = [e["kind"] for e in events]
        assert "plan" in kinds and "analysis" in kinds and "approval_request" in kinds
        approval = request_of(s, turn, "approve")
        assert "+# note" in approval["diff"]

        trace = get(s.app, f"/api/trace?turn={turn}").json  # the trace is real, not copied
        assert trace["decisions"] and trace["calls"] and trace["changes"]
        for made in trace["calls"]:  # what the real calls took and used is shown, not 0 and ?
            assert (made["latency_ms"], made["prompt_tokens"], made["completion_tokens"]) == (
                1234,
                55,
                7,
            )

        refused = post(s, "/api/chat/send", {"session": sessions[0]["id"], "text": "another one"})
        assert refused.status == 409 and "recorded requests" in refused.json["error"]

        no_model = post(s, "/api/change/revise", body_for(approval, note="try again"))
        assert no_model.status == 409 and "no recorded revision" in no_model.json["error"]

        ok = post(s, "/api/change/approve", body_for(approval, note="read it"))  # still unspent
        assert ok.status == 200, ok.raw  # the button works, as in a live run
        assert call(s.app, "GET", "/api/meta", token=TOKEN).json["demo"] is True
        assert request_of(s, turn, "pr")


def test_the_sample_project_becomes_a_git_repository_and_its_check_passes(tmp_path: Path) -> None:
    dest = tmp_path / "shop-api"
    make_git_sample(DEMO / "shop-api", dest, "https://github.com/someone/shop-api-demo.git")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=dest, capture_output=True, text=True, check=True
    )
    assert len(log.stdout.strip().splitlines()) == 1
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=dest, capture_output=True, text=True, check=True
    )
    assert remote.stdout.strip() == "https://github.com/someone/shop-api-demo.git"
    ran = subprocess.run(
        ["python", "check_shop.py"], cwd=dest, capture_output=True, text=True, check=False
    )
    assert ran.returncode == 0, ran.stderr
    with pytest.raises(ReplayError):  # an existing folder is never overwritten
        make_git_sample(DEMO / "shop-api", dest)


@pytest.mark.parametrize("path", sorted(DEMO.glob("*-recording.json")))
def test_each_saved_recording_is_valid_and_holds_no_machine_text_or_key(path: Path) -> None:
    rec = load_recording(path)
    assert rec.replies and rec.mode in ("ask", "change")
    text = path.read_text(encoding="utf-8")
    for local in ("mpran", "cogmesh-testing", "E:\\\\", "C:\\\\", "gsk_", "AIza", "sk-"):
        assert local not in text
