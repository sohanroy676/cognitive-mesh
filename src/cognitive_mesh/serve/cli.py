"""The `serve` command (ADR-0044): chat, workspace graph and data browser on three local ports.

All three servers listen on 127.0.0.1 only and share one access token per start. Only the chat
server accepts writes, and only the actions listed in ADR-0044, each behind an approval request.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitive_mesh.serve.app_api import AppApi
from cognitive_mesh.serve.chat_store import ChatStore, ChatStoreError
from cognitive_mesh.serve.config import (
    DEFAULT_APP_PORT,
    DEFAULT_DATA_PORT,
    DEFAULT_GRAPH_PORT,
    ServeConfig,
)
from cognitive_mesh.serve.data_api import DataApi
from cognitive_mesh.serve.graph_api import GraphApi
from cognitive_mesh.serve.http import LocalServer, Reply, Request, new_token
from cognitive_mesh.serve.replay import (
    ReplayError,
    ReplayModels,
    load_recording,
    run_recordings,
    write_replay_catalog,
)
from cognitive_mesh.serve.ws_cache import WorkspaceGraphCache

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_SERVE_ERROR = 26

COMMON = {"/common.js": "common.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
# Without --catalog the chat uses this catalog when it exists: Groq coordinates and also works,
# the local llama3.2 is a second worker.
DEFAULT_CATALOG = Path("docs") / "serve-catalog.toml"
DEFAULT_CONDUCTOR = "groq"


@dataclass
class Servers:
    app: LocalServer
    graph: LocalServer
    data: LocalServer
    api: AppApi
    chat: ChatStore
    token: str

    def close(self) -> None:
        for server in (self.app, self.graph, self.data):
            server.server_close()
        self.api.wait(5)
        self.chat.close()


def build_servers(config: ServeConfig, token: str | None = None) -> Servers:
    """Create (but do not start) the three servers. Port 0 picks a free port."""
    token = token or new_token()
    chat = ChatStore(config.chat)
    ports: dict[str, int] = {}

    def meta(_req: Request) -> Reply:
        return 200, {"token": token, "ports": ports, "demo": config.demo is not None}

    project_graph = WorkspaceGraphCache(
        config.workspace, config.home / "graph-cache" / "workspace.json"
    )
    api = AppApi(config, chat, project_graph)
    servers: list[LocalServer] = []
    try:
        app = LocalServer(
            port=config.app_port,
            token=token,
            cookie="cogmesh_app",
            pages={"/": "app.html", "/app.js": "app.js", **COMMON},
            gets={**api.routes_get(), "/api/meta": meta},
            posts=api.routes_post(),
        )
        servers.append(app)
        graph = LocalServer(
            port=config.graph_port,
            token=token,
            cookie="cogmesh_graph",
            pages={
                "/": "graph.html",
                "/graph.js": "graph.js",
                "/filegraph.js": "filegraph.js",
                **COMMON,
            },
            gets={**GraphApi(config, project_graph).routes(), "/api/meta": meta},
        )
        servers.append(graph)
        data = LocalServer(
            port=config.data_port,
            token=token,
            cookie="cogmesh_data",
            pages={"/": "data.html", "/data.js": "data.js", **COMMON},
            gets={**DataApi(config).routes(), "/api/meta": meta},
        )
    except OSError:
        for server in servers:
            server.server_close()
        chat.close()
        raise
    ports.update(app=app.port, graph=graph.port, data=data.port)
    return Servers(app, graph, data, api, chat, token)


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "serve", help="run the local web app: chat, workspace graph and data browser (ADR-0044)"
    )
    p.add_argument("--home", type=Path, default=Path("cogmesh-home"), help="folder for all files")
    p.add_argument("--catalog", type=Path, default=None, help="model catalog (TOML) for the chat")
    p.add_argument("--conductor", default=None, metavar="ID", help="catalog entry that plans turns")
    p.add_argument("--app-port", type=int, default=DEFAULT_APP_PORT)
    p.add_argument("--graph-port", type=int, default=DEFAULT_GRAPH_PORT)
    p.add_argument("--data-port", type=int, default=DEFAULT_DATA_PORT)
    p.add_argument(
        "--allow-unisolated-checks",
        action="store_true",
        help="run a project's declared check command; it runs with your privileges, NOT isolated",
    )
    p.add_argument("--no-browser", action="store_true", help="do not open the chat page")
    p.add_argument(
        "--replay",
        type=Path,
        action="append",
        default=None,
        metavar="FILE",
        help="demo: play back recorded model replies (see demo/); needs no model, key or internet",
    )
    p.add_argument(
        "--capture",
        type=Path,
        default=None,
        metavar="DIR",
        help="write the models' replies of every turn to DIR, to make a recording from",
    )


def _make_embedder() -> Any:
    from cognitive_mesh.memory import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()


def _model_problems(config: ServeConfig) -> list[str]:
    """Why a catalog entry cannot be used right now (a missing key or opt-in), one line each."""
    from cognitive_mesh.config import ConfigError, load_settings
    from cognitive_mesh.providers import CatalogError, load_catalog
    from cognitive_mesh.providers.factory import entry_problem

    try:
        settings = load_settings(
            provider="catalog", model=config.conductor, model_catalog=config.catalog
        )
        assert settings.model_catalog is not None  # noqa: S101 - required with catalog
        catalog = load_catalog(settings.model_catalog)
    except (ConfigError, CatalogError) as exc:
        return [f"the model catalog cannot be used: {exc}"]
    out = []
    for entry_id, entry in catalog.models.items():
        problem = entry_problem(settings, entry)
        if problem:
            out.append(f"model {entry_id} is not usable: {problem}")
    return out


def run(args: argparse.Namespace) -> int:
    if (args.catalog is None) != (args.conductor is None):
        print("--catalog and --conductor go together", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    catalog, conductor = args.catalog, args.conductor
    if catalog is None and DEFAULT_CATALOG.is_file():
        catalog, conductor = DEFAULT_CATALOG, DEFAULT_CONDUCTOR
    recordings = []
    for path in args.replay or []:
        try:
            recordings.append(load_recording(path))
        except ReplayError as exc:
            print(f"--replay {path}: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    demo = ReplayModels(recordings) if recordings else None
    if demo is not None:
        try:
            catalog = write_replay_catalog(args.catalog or DEFAULT_CATALOG, args.home)
        except ReplayError as exc:
            print(f"--replay: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        conductor = recordings[0].conductor
    config = ServeConfig(
        home=args.home,
        catalog=catalog,
        conductor=conductor,
        app_port=args.app_port,
        graph_port=args.graph_port,
        data_port=args.data_port,
        allow_unisolated_checks=args.allow_unisolated_checks,
        embedder_factory=_make_embedder,
        client_factory=demo,
        demo=demo,
        capture_dir=args.capture,
    )
    try:
        servers = build_servers(config)
    except OSError:
        print("cannot listen on the ports; is one in use?", file=sys.stderr)
        return EXIT_SERVE_ERROR
    except ChatStoreError as exc:
        print(f"chat file: {exc}", file=sys.stderr)
        return EXIT_SERVE_ERROR
    for server in (servers.graph, servers.data):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"chat   {servers.app.url()}", flush=True)
    print(f"graph  {servers.graph.url()}", flush=True)
    print(f"data   {servers.data.url()}", flush=True)
    print("the URLs hold this session's access token; stop with Ctrl+C", flush=True)
    if args.allow_unisolated_checks:
        print(
            "note: declared checks run with your privileges and are not isolated", file=sys.stderr
        )
    if config.catalog is None:
        print("note: no model is configured, so the chat cannot start a turn", file=sys.stderr)
    else:
        print(f"models: {config.catalog} (conductor: {config.conductor})", flush=True)
        for line in _model_problems(config):
            print(f"note: {line}", file=sys.stderr, flush=True)
    if recordings:
        print(
            f"demo: replaying {len(recordings)} recorded chat(s) with recorded model replies",
            flush=True,
        )
        threading.Thread(
            target=run_recordings, args=(servers.api, recordings, args.home), daemon=True
        ).start()
    if not args.no_browser:
        webbrowser.open(servers.app.url())
    try:
        servers.app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servers.close()
    return EXIT_OK
