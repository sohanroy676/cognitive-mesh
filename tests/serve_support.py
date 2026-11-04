"""Helpers for the ADR-0044 tests: the three servers on free ports, a client, scripted models."""

import http.client
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cognitive_mesh.providers import ModelClient, ModelRequest, ModelResponse
from cognitive_mesh.serve.cli import Servers, build_servers
from cognitive_mesh.serve.config import ServeConfig
from conductor_support import EXPLAIN_OK, ScriptedClient, reply
from eval_support import Script
from memory_support import FakeEmbedder

TOKEN = "tkn-for-tests"
CATALOG = """\
[models.cond]
wire = "ollama"
base_url = "http://localhost:11434"
model = "llama3.2"
context_tokens = 8192
tier = "free"
data_use = "no_training"
roles = ["conductor"]

[models.worker]
wire = "ollama"
base_url = "http://localhost:11434"
model = "llama3.2"
context_tokens = 8192
tier = "free"
data_use = "no_training"
roles = ["coder", "code_explainer", "reader"]
"""

ANALYSIS_STEPS = [
    {"step_id": "read", "capability": "explain_file", "inputs": {"path": "app.py"}},
]
ANALYSIS_ASSIGN = {"read": {"model": "worker", "reason": "a small edit to one file"}}


class Worker(Script):
    """Answers the coder's request with a diff and every other request with an explanation."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        if '"diff"' in request.system:
            return super().complete(request)
        self.requests.append(request)
        return ModelResponse(provider="fake", model="m", text=EXPLAIN_OK)


class Models:
    """One scripted client per catalog entry id; the conductor repeats its plan."""

    def __init__(self, conductor_reply: str | None = None) -> None:
        plan = conductor_reply or reply(ANALYSIS_STEPS, ANALYSIS_ASSIGN, "one file matters")
        self.conductor: Any = ScriptedClient(plan, repeat=True)
        self.worker: Any = Worker()

    def __call__(self, entry_id: str) -> ModelClient:
        client: ModelClient = self.conductor if entry_id == "cond" else self.worker
        return client


def config_for(home: Path, models: Models | None = None, **kw: Any) -> ServeConfig:
    catalog = home / "catalog.toml"
    home.mkdir(parents=True, exist_ok=True)
    catalog.write_text(CATALOG, encoding="utf-8")
    fake = FakeEmbedder()
    return ServeConfig(
        home=home / "data",
        catalog=kw.pop("catalog", catalog),
        conductor=kw.pop("conductor", "cond"),
        app_port=0,
        graph_port=0,
        data_port=0,
        embedder_factory=lambda: fake,
        client_factory=models or Models(),
        **kw,
    )


@contextmanager
def running(config: ServeConfig, token: str = TOKEN) -> Iterator[Servers]:
    servers = build_servers(config, token)
    threads = [
        threading.Thread(target=s.serve_forever, daemon=True)
        for s in (servers.app, servers.graph, servers.data)
    ]
    for t in threads:
        t.start()
    try:
        yield servers
    finally:
        for s in (servers.app, servers.graph, servers.data):
            s.shutdown()
        servers.close()


class Reply:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status, self.headers, self.raw = status, headers, body

    @property
    def json(self) -> Any:
        return json.loads(self.raw.decode("utf-8"))


def call(
    server: Any,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    raw: bytes | None = None,
    token: str | None = None,
    origin: bool = True,
    json_type: bool = True,
) -> Reply:
    """One request. By default a POST carries the right Origin, token header and content type."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    sent: dict[str, str] = dict(headers or {})
    if token is not None:
        sent.setdefault("X-Cogmesh-Token", token)
    payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    if method == "POST":
        if origin:
            sent.setdefault("Origin", f"http://127.0.0.1:{server.port}")
        if json_type:
            sent.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=payload, headers=sent)
        resp = conn.getresponse()
        data = resp.read()
        return Reply(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    finally:
        conn.close()


def wait_for(check: Callable[[], bool], seconds: float = 60.0) -> bool:
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False
