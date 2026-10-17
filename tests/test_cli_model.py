"""`run` with a model step against a fake Ollama on 127.0.0.1 (the real client and transport)."""

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cognitive_mesh.agents import default_registry
from cognitive_mesh.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_TASK_NOT_SUCCEEDED,
    _registry_for,
    main,
)
from cognitive_mesh.orchestrator import TaskPlan, plan_task
from cognitive_mesh.providers.http import HttpReply

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"
DEP_FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
PLANS = Path(__file__).parent / "fixtures" / "plans"
FILE_CANARY = "sample-app"  # only in the explained file; must never be echoed
BODY_CANARY = "CANARY-SERVER-BODY-4417"
SUMMARY = '{"summary": "A tiny sample project.", "symbols": []}'
OK_BODY = json.dumps({"message": {"role": "assistant", "content": SUMMARY}, "done": True}).encode()


@contextmanager
def _ollama(status: int = 200, body: bytes = OK_BODY) -> Iterator[tuple[str, list[bytes]]]:
    seen: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            seen.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _run(*extra: str) -> int:
    return main(["run", str(PLANS / "needs_model.json"), "--root", str(FIXTURE), *extra])


def test_a_model_step_succeeds_as_model_unverified(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    db = tmp_path / "t.sqlite3"
    with _ollama() as (url, seen):
        monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
        monkeypatch.setenv("COGMESH_OLLAMA_BASE_URL", url)
        assert _run("--json", "--trace-db", str(db)) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "succeeded"
    attempt = payload["steps"][1]["attempts"][0]
    assert attempt["basis"] == "model_unverified"
    assert attempt["model"] == {"provider": "ollama", "model": "llama3.2"}
    request = json.loads(seen[0])
    assert request["model"] == "llama3.2"
    assert FILE_CANARY in json.loads(request["messages"][1]["content"])["content"]  # sent as data
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT model_provider, model_name FROM attempts").fetchall()
    assert ("ollama", "llama3.2") in rows


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (500, "provider_unavailable"),
        (429, "provider_error"),
        (404, "provider_error"),
        (401, "provider_error"),
        (200, "provider_error"),  # body is not a model reply
    ],
)
def test_provider_failures_are_explicit_and_leak_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    status: int,
    expected: str,
) -> None:
    db = tmp_path / "t.sqlite3"
    with _ollama(status, BODY_CANARY.encode()) as (url, _):
        monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
        monkeypatch.setenv("COGMESH_OLLAMA_BASE_URL", url)
        assert _run("--trace-db", str(db)) == EXIT_TASK_NOT_SUCCEEDED
    captured = capsys.readouterr()
    assert expected in captured.out
    assert "blocked by: explain" in captured.out
    for text in (captured.out, captured.err, db.read_bytes().decode("latin-1")):
        assert BODY_CANARY not in text
        assert FILE_CANARY not in text


def test_a_retryable_failure_is_retried_within_the_plan_policy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _ollama(503, b"") as (url, seen):
        monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
        monkeypatch.setenv("COGMESH_OLLAMA_BASE_URL", url)
        assert _run("--json") == EXIT_TASK_NOT_SUCCEEDED
    step = json.loads(capsys.readouterr().out)["steps"][1]
    assert step["attempt_count"] == len(seen) == 2


def test_no_provider_configured_keeps_the_provider_unavailable_behavior(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run() == EXIT_TASK_NOT_SUCCEEDED
    assert "provider_unavailable" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env",
    [
        {"COGMESH_PROVIDER": "bogus"},
        {"COGMESH_PROVIDER": "groq"},  # key missing
        {"COGMESH_PROVIDER": "groq", "GROQ_API_KEY": "gsk_fake x"},  # unsafe key characters
        {"COGMESH_PROVIDER": "groq", "GROQ_API_KEY": "gsk_fake", "COGMESH_GROQ_MODEL": "a b"},
        {"COGMESH_PROVIDER": "ollama", "COGMESH_MODEL_TIMEOUT_S": "0"},
        {"COGMESH_MODEL_TIMEOUT_S": "0"},
    ],
    ids=range(6),
)
def test_a_model_plan_with_bad_or_unsupported_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env: dict[str, str]
) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _run() == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "gsk_fake" not in captured.out + captured.err
    assert "task explain" not in captured.out  # nothing ran


def test_plans_without_model_steps_never_read_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "bogus")
    monkeypatch.setenv("COGMESH_MODEL_TIMEOUT_S", "-5")
    assert main(["run", str(PLANS / "ok.json"), "--root", str(DEP_FIXTURE)]) == EXIT_OK


def test_other_commands_do_not_read_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "bogus")
    assert main(["agents"]) == EXIT_OK
    assert main(["ingest", str(FIXTURE)]) == EXIT_OK


def test_the_configured_timeout_reaches_the_code_explainer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = {"step_id": "e", "capability": "explain_file", "inputs": {"path": "x"}}
    plan = TaskPlan.model_validate({"task_id": "t", "steps": [step]})
    validated = plan_task(plan, default_registry())
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    monkeypatch.setenv("COGMESH_MODEL_TIMEOUT_S", "77")
    explainer = _registry_for(validated).get("code_explainer")
    assert explainer._timeout_s == 77.0  # type: ignore[attr-defined]  # noqa: SLF001


# ---- Groq (ADR-0013): the real client with a faked transport; no network ----

GROQ_KEY = "gsk_" + "CANARY" + "KEY0123456789"
GROQ_OK = json.dumps(
    {"choices": [{"message": {"role": "assistant", "content": SUMMARY}, "finish_reason": "stop"}]}
).encode()


class _FakeGroqTransport:
    status = 200
    body = GROQ_OK
    calls: list[tuple[str, dict[str, str], bytes]] = []  # noqa: RUF012 - reset per test

    def post_json(
        self, url: str, body: bytes, headers: Mapping[str, str], **_: object
    ) -> HttpReply:
        type(self).calls.append((url, dict(headers), body))
        return HttpReply(type(self).status, type(self).body)


@pytest.fixture
def groq_env(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGroqTransport]:
    class Fake(_FakeGroqTransport):
        calls: list[tuple[str, dict[str, str], bytes]] = []  # noqa: RUF012

    monkeypatch.setattr("cognitive_mesh.providers.groq.StdlibTransport", Fake)
    monkeypatch.setenv("COGMESH_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    return Fake


def test_groq_model_step_succeeds_as_model_unverified(
    groq_env: type[_FakeGroqTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    db = tmp_path / "t.sqlite3"
    assert _run("--json", "--trace-db", str(db)) == EXIT_OK
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "succeeded"
    attempt = payload["steps"][1]["attempts"][0]
    assert attempt["basis"] == "model_unverified"
    assert attempt["model"] == {"provider": "groq", "model": "qwen/qwen3.8-27b"}
    url, headers, body = groq_env.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {GROQ_KEY}"
    assert json.loads(body)["reasoning_effort"] == "none"
    assert GROQ_KEY not in out + db.read_bytes().decode("latin-1")


@pytest.mark.parametrize(
    ("status", "expected", "attempts"),
    [
        (401, "provider_error", 1),  # auth is not retried
        (429, "provider_error", 2),  # retried within the plan policy, then reported
        (500, "provider_unavailable", 2),
    ],
)
def test_groq_failures_are_explicit_bounded_and_leak_nothing(
    groq_env: type[_FakeGroqTransport],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    status: int,
    expected: str,
    attempts: int,
) -> None:
    groq_env.status = status
    groq_env.body = f"{BODY_CANARY} {GROQ_KEY}".encode()
    db = tmp_path / "t.sqlite3"
    assert _run("--trace-db", str(db)) == EXIT_TASK_NOT_SUCCEEDED
    captured = capsys.readouterr()
    assert expected in captured.out
    assert len(groq_env.calls) == attempts
    for text in (captured.out, captured.err, db.read_bytes().decode("latin-1")):
        for secret in (BODY_CANARY, GROQ_KEY, FILE_CANARY):
            assert secret not in text


def test_groq_without_a_key_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "groq")
    assert _run() == EXIT_CONFIG_ERROR
    assert "GROQ_API_KEY" in capsys.readouterr().err
