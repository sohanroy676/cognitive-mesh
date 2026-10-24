"""Shared setup for the web UI tests: a real world built through the CLI, and a raw HTTP client."""

import hashlib
import http.client
import json
import shutil
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitive_mesh.cli import main
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.ui.config import UiConfig
from cognitive_mesh.ui.server import UiServer, make_server
from memory_support import FakeEmbedder

FIXTURES = Path(__file__).parent / "fixtures"
A = "module:src/dep_app/a.py"
UTIL = "module:src/dep_app/util.py"
APP_ID = "dep-app@app"
A_UTIL = claim_id("depends_on", A, UTIL, "imports", APP_ID)
QUERY = "dependencies_of module:src/dep_app/a.py"
TOKEN = "test-token-0123456789"  # noqa: S105 - a test value
WRONG_TOKEN = "wrong-token"  # noqa: S105 - a test value


@dataclass
class World:
    app: Path
    db: Path
    idx: Path
    graph: Path
    reviews: Path
    outcomes: Path

    def config(self, **over: object) -> UiConfig:
        base: dict[str, object] = {
            "db": self.db,
            "graph": self.graph,
            "index": self.idx,
            "reviews": self.reviews,
            "outcomes": self.outcomes,
            "root": self.app,
            "port": 0,
            "embedder_factory": FakeEmbedder,
        }
        return UiConfig(**{**base, **over})

    def files(self) -> list[Path]:
        paths = [self.db, self.graph, self.reviews, self.outcomes]
        paths += sorted(p for p in self.idx.rglob("*") if p.is_file())
        paths += sorted(p for p in self.app.rglob("*") if p.is_file())
        return paths


def run(*argv: str | Path) -> int:
    return main([str(a) for a in argv])


def build_world(tmp_path: Path) -> World:
    """Two runs, an edit (a.py no longer imports b), one more run, index, graph, review, outcome."""
    app = tmp_path / "app"
    shutil.copytree(FIXTURES / "dep_app", app)
    w = World(
        app,
        tmp_path / "t.sqlite3",
        tmp_path / "idx",
        tmp_path / "g.sqlite3",
        tmp_path / "r.sqlite3",
        tmp_path / "o.sqlite3",
    )
    plan = FIXTURES / "plans" / "ok.json"
    for _ in range(2):
        assert run("run", plan, "--root", app, "--trace-db", w.db) == 0
    a = app / "src" / "dep_app" / "a.py"
    a.write_text(a.read_text().replace("from . import b", "from . import util"), newline="\n")
    assert run("run", plan, "--root", app, "--trace-db", w.db) == 0
    assert run("memory", "index", "--db", w.db, "--index", w.idx) == 0
    assert run("experience", "build", "--db", w.db, "--graph", w.graph) == 0
    review: list[str | Path] = ["experience", "review", A_UTIL, "--verdict", "confirmed"]
    review += ["--note", "looks right"]
    assert (
        run(*review, "--by", "me", "--root", app, "--graph", w.graph, "--reviews", w.reviews) == 0
    )
    outcome: list[str | Path] = ["experience", "outcome", A_UTIL, "--source", "check"]
    outcome += ["--result", "succeeded"]
    outcome += ["--detail", "tests passed", "--by", "me", "--root", app, "--graph", w.graph]
    assert run(*outcome, "--outcomes", w.outcomes) == 0
    return w


def digest(paths: list[Path]) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class Client:
    def __init__(self, server: UiServer) -> None:
        self.server = server

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        token: str | None = TOKEN,
        host: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Reply:
        port = self.server.port
        sent = {"Host": host if host is not None else f"127.0.0.1:{port}", **(headers or {})}
        if token is not None and "Cookie" not in sent:
            sent["Cookie"] = f"cogmesh_token={token}"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            conn.request(method, path, headers=sent)
            resp = conn.getresponse()
            return Reply(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        finally:
            conn.close()

    def get(self, path: str, **kw: object) -> Reply:
        return self.request(path, **kw)  # type: ignore[arg-type]


@contextmanager
def serving(config: UiConfig) -> Iterator[Client]:
    server = make_server(config, token=TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
