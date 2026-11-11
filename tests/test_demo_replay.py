"""The saved Discount example shows offline, and the sample project sets up as a git repository."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cognitive_mesh.serve.chat_store import ChatStore
from cognitive_mesh.serve.replay import ReplayError, load_recording, seed_replay
from serve_support import TOKEN, call, config_for, running

DEMO = Path(__file__).parent.parent / "demo"


def script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, DEMO / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recording_is_valid_and_holds_no_machine_text() -> None:
    recording = load_recording(DEMO / "discount-example.json")
    kinds = [e.kind for e in recording.events]
    assert recording.mode == "change" and "plan" in kinds and "approval_request" in kinds
    text = (DEMO / "discount-example.json").read_text(encoding="utf-8")
    for local in ("mpran", "cogmesh-testing", "E:\\\\", "C:\\\\"):
        assert local not in text


def test_seeding_is_finished_labelled_and_idempotent(tmp_path: Path) -> None:
    chat = ChatStore(tmp_path / "chat.sqlite3")
    recording = load_recording(DEMO / "discount-example.json")
    first = seed_replay(chat, recording)
    assert seed_replay(chat, recording) == first
    assert len(chat.sessions()) == 1
    turn = chat.turn(first)
    assert turn is not None and turn["status"] == "done"
    events = chat.events(first)
    assert events[0]["kind"] == "replay" and events[-1]["kind"] == "done"
    assert chat.open_approvals(first) == []  # no button can act on a recording
    chat.close()


def test_the_app_serves_the_recording_without_a_model(tmp_path: Path) -> None:
    recording = load_recording(DEMO / "discount-example.json")
    with running(config_for(tmp_path, catalog=None, conductor=None)) as s:
        turn_id = seed_replay(s.chat, recording)
        sessions = call(s.app, "GET", "/api/sessions", token=TOKEN).json["sessions"]
        got = call(s.app, "GET", f"/api/session?id={sessions[0]['id']}", token=TOKEN).json
        assert got["turns"][0]["id"] == turn_id and got["turns"][0]["approvals"] == []
        refused = call(
            s.app,
            "POST",
            "/api/change/approve",
            token=TOKEN,
            body={"approval_id": "x" * 32, "change_id": "c", "diff_sha256": "d" * 64, "note": ""},
        )
        assert refused.status >= 400


@pytest.mark.parametrize("content", ["not json", "{}", json.dumps({"title": "t"})])
def test_a_broken_recording_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "r.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ReplayError):
        load_recording(path)
    with pytest.raises(ReplayError):
        load_recording(tmp_path / "missing.json")


def test_the_sample_project_becomes_a_git_repository_and_its_check_passes(tmp_path: Path) -> None:
    dest = tmp_path / "shop-api"
    script("make_shop_api").make(dest, "https://github.com/someone/shop-api-demo.git")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=dest, capture_output=True, text=True, check=True
    )
    assert len(log.stdout.strip().splitlines()) == 1
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=dest, capture_output=True, text=True, check=True
    )
    assert remote.stdout.strip() == "https://github.com/someone/shop-api-demo.git"
    ran = subprocess.run(
        [sys.executable, "check_shop.py"], cwd=dest, capture_output=True, text=True, check=False
    )
    assert ran.returncode == 0, ran.stderr
    with pytest.raises(SystemExit):  # an existing folder is never overwritten
        script("make_shop_api").make(dest)
