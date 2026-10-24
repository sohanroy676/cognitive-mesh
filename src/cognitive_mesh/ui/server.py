"""The local, read-only, GET-only web server (ADR-0031).

Binds 127.0.0.1 only. Every request is checked for the Host header and the access token before
routing. Responses carry a restrictive CSP. Nothing here writes to a store.
"""

from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from cognitive_mesh.ui.api import UiApi, error_reply
from cognitive_mesh.ui.config import UiConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

BIND_HOST = "127.0.0.1"
TOKEN_COOKIE = "cogmesh_token"  # noqa: S105 - a cookie name, not a secret
TOKEN_HEADER = "X-Cogmesh-Token"  # noqa: S105 - a header name, not a secret
MAX_PATH_CHARS = 4096
MAX_PARAM_CHARS = 2100  # a query is at most 2000 characters (memory.index.MAX_QUERY_CHARS)

# Fixed allowlist: a request path never becomes a file name.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "frame-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'"
)
# The ADR-0008 graph page styles itself inline and runs no script.
GRAPH_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"


class UiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows reuse would let a second server share a busy port

    def __init__(self, config: UiConfig, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(32)
        self.api = UiApi.create(config)
        super().__init__((BIND_HOST, config.port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}/?token={self.token}"


def make_server(config: UiConfig, token: str | None = None) -> UiServer:
    return UiServer(config, token)


class _Handler(BaseHTTPRequestHandler):
    server: UiServer
    server_version = "cognitive-mesh-ui"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silent: request lines can carry the token."""

    # -- responses ----------------------------------------------------------------------

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        csp: str = PAGE_CSP,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        self._send(status, body, "application/json")

    def _refuse(self, status: HTTPStatus, message: str, **extra: str) -> None:
        body = json.dumps({"error": message}).encode("ascii")
        self._send(status.value, body, "application/json", extra=extra)

    # -- checks -------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        port = self.server.port
        return self.headers.get("Host") in {f"{BIND_HOST}:{port}", f"localhost:{port}"}

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and secrets.compare_digest(
            supplied.encode("utf-8"), self.server.token.encode("utf-8")
        )

    def _request_token(self) -> str | None:
        header = self.headers.get(TOKEN_HEADER)
        if header is not None:
            return header
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar: SimpleCookie = SimpleCookie(raw)
        except Exception:  # a malformed Cookie header means no token
            return None
        morsel = jar.get(TOKEN_COOKIE)
        return morsel.value if morsel else None

    # -- methods ------------------------------------------------------------------------

    def do_GET(self) -> None:
        if len(self.path) > MAX_PATH_CHARS:
            self._refuse(HTTPStatus.REQUEST_URI_TOO_LONG, "request too long")
            return
        if not self._host_ok():
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        parts = urlsplit(self.path)
        params = {
            k: v[0][:MAX_PARAM_CHARS]
            for k, v in parse_qs(parts.query, keep_blank_values=True, max_num_fields=20).items()
        }
        if parts.path in STATIC_FILES and "token" in params:
            self._start_session(params["token"])
            return
        if not self._token_ok(self._request_token()):
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if parts.path in STATIC_FILES:
            self._static(parts.path)
        elif parts.path == "/api/graph.html":
            self._graph(params)
        elif parts.path in (routes := self.server.api.routes()):
            self._api(routes[parts.path], params)
        else:
            self._refuse(HTTPStatus.NOT_FOUND, "not found")

    def _method_not_allowed(self) -> None:
        if not self._host_ok():
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        self._refuse(HTTPStatus.METHOD_NOT_ALLOWED, "read-only: only GET is served", Allow="GET")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _method_not_allowed

    # -- routes -------------------------------------------------------------------------

    def _start_session(self, supplied: str) -> None:
        if not self._token_ok(supplied):
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        cookie = f"{TOKEN_COOKIE}={self.server.token}; Path=/; HttpOnly; SameSite=Strict"
        self._send(
            HTTPStatus.FOUND.value,
            b"",
            "text/plain; charset=utf-8",
            extra={"Location": "/", "Set-Cookie": cookie},
        )

    def _static(self, path: str) -> None:
        name, content_type = STATIC_FILES[path]
        data = resources.files("cognitive_mesh.ui").joinpath("static", name).read_bytes()
        self._send(HTTPStatus.OK.value, data, content_type)

    def _api(self, route: Any, params: Mapping[str, str]) -> None:
        try:
            status, payload = route(params)
        except Exception as exc:
            status, payload = error_reply(exc)
        self._json(status, payload)

    def _graph(self, params: Mapping[str, str]) -> None:
        try:
            page = self.server.api.graph_html(params)
        except Exception as exc:
            status, payload = error_reply(exc)
            self._json(status, payload)
            return
        self._send(
            HTTPStatus.OK.value, page.encode("ascii"), "text/html; charset=utf-8", csp=GRAPH_CSP
        )
