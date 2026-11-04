"""The HTTP base shared by the three local servers (ADR-0044).

All servers bind 127.0.0.1 only. Every request passes the Host check and the access token before
routing. A server that is created with `allow_post=True` (the chat server only) also accepts POST
under stricter rules: an Origin equal to its own, the token in a header (a cookie alone is not
enough, so a cross-site form cannot ride it), a JSON object body of at most 64 KB. Every other
method answers 405. Responses carry a restrictive CSP and are never cached.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

BIND_HOST = "127.0.0.1"
TOKEN_HEADER = "X-Cogmesh-Token"  # noqa: S105 - a header name, not a secret
MAX_PATH_CHARS = 4096
MAX_PARAM_CHARS = 2100
MAX_BODY_BYTES = 64 * 1024
MAX_DRAIN_BYTES = 1024 * 1024

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

Reply = tuple[int, dict[str, Any]]


@dataclass(frozen=True)
class Request:
    params: Mapping[str, str]
    body: Mapping[str, Any] | None = None


Handler = Callable[[Request], Reply]

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows reuse would let a second server share a busy port

    def __init__(
        self,
        *,
        port: int,
        token: str,
        cookie: str,
        pages: Mapping[str, str],
        gets: Mapping[str, Handler],
        posts: Mapping[str, Handler] | None = None,
    ) -> None:
        self.token = token
        self.cookie = cookie
        self.pages = dict(pages)  # request path -> file name in cognitive_mesh/serve/static
        self.gets = dict(gets)
        self.posts = dict(posts or {})
        super().__init__((BIND_HOST, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}/?token={self.token}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


class _Handler(BaseHTTPRequestHandler):
    server: LocalServer
    server_version = "cognitive-mesh-serve"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silent: request lines can carry the token."""

    # -- responses ----------------------------------------------------------------------

    def _send(
        self, status: int, body: bytes, content_type: str, extra: Mapping[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", PAGE_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        self._send(status, body, "application/json")

    def _refuse(self, status: HTTPStatus, message: str, **extra: str) -> None:
        body = json.dumps({"error": message}).encode("ascii")
        self._send(status.value, body, "application/json", extra)

    # -- checks -------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        port = self.server.port
        return self.headers.get("Host") in {f"{BIND_HOST}:{port}", f"localhost:{port}"}

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and secrets.compare_digest(
            supplied.encode("utf-8"), self.server.token.encode("utf-8")
        )

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar: SimpleCookie = SimpleCookie(raw)
        except Exception:  # a malformed Cookie header means no token
            return None
        morsel = jar.get(self.server.cookie)
        return morsel.value if morsel else None

    def _origin_ok(self) -> bool:
        port = self.server.port
        return self.headers.get("Origin") in {
            f"http://{BIND_HOST}:{port}",
            f"http://localhost:{port}",
        }

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
        if parts.path in self.server.pages and "token" in params:
            self._start_session(params["token"])
            return
        supplied = self.headers.get(TOKEN_HEADER)
        if supplied is None:
            supplied = self._cookie_token()
        if not self._token_ok(supplied):
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if parts.path in self.server.pages:
            self._static(self.server.pages[parts.path])
        elif parts.path in self.server.gets:
            self._call(self.server.gets[parts.path], Request(params))
        else:
            self._refuse(HTTPStatus.NOT_FOUND, "not found")

    def _read_body(self) -> tuple[int, bytes]:
        """(declared length or -1, up to MAX_BODY_BYTES of the body). The body is always taken off
        the socket before any answer, so an early refusal never leaves unread bytes behind (on
        Windows that resets the connection before the client reads the answer)."""
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return -1, b""
        raw = self.rfile.read(min(length, MAX_BODY_BYTES))
        left = length - len(raw)
        while left > 0 and left <= MAX_DRAIN_BYTES:  # discard an oversized body
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        if left > 0:
            self.close_connection = True
        return length, raw

    def do_POST(self) -> None:
        if not self.server.posts:
            self._not_allowed()
            return
        length, raw = self._read_body()
        # the order of these checks is part of the contract (ADR-0044 decision 1)
        if not self._host_ok() or not self._origin_ok():
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not self._token_ok(self.headers.get(TOKEN_HEADER)):
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        path = urlsplit(self.path).path
        handler = self.server.posts.get(path)
        if handler is None:
            self._refuse(HTTPStatus.NOT_FOUND, "not found")
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._refuse(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send application/json")
            return
        if length < 0:
            self._refuse(HTTPStatus.LENGTH_REQUIRED, "a Content-Length is required")
            return
        if length > MAX_BODY_BYTES:
            self._refuse(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the body is too large")
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            self._refuse(HTTPStatus.BAD_REQUEST, "the body is not valid JSON")
            return
        if not isinstance(body, dict):
            self._refuse(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
            return
        self._call(handler, Request({}, body))

    def _not_allowed(self) -> None:
        self._read_body()
        if not self._host_ok():
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        self._refuse(HTTPStatus.METHOD_NOT_ALLOWED, "this server is read-only", Allow="GET")

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed

    # -- routes -------------------------------------------------------------------------

    def _start_session(self, supplied: str) -> None:
        if not self._token_ok(supplied):
            self._refuse(HTTPStatus.FORBIDDEN, "forbidden")
            return
        cookie = f"{self.server.cookie}={self.server.token}; Path=/; HttpOnly; SameSite=Strict"
        self._send(
            HTTPStatus.FOUND.value,
            b"",
            "text/plain; charset=utf-8",
            {"Location": "/", "Set-Cookie": cookie},
        )

    def _static(self, name: str) -> None:
        data = resources.files("cognitive_mesh.serve").joinpath("static", name).read_bytes()
        suffix = name[name.rfind(".") :]
        self._send(HTTPStatus.OK.value, data, STATIC_TYPES[suffix])

    def _call(self, handler: Handler, request: Request) -> None:
        try:
            status, payload = handler(request)
        except Exception as exc:
            status, payload = error_reply(exc)
        self._json(status, payload)


class ApiError(Exception):
    """An error whose message is fixed text safe to show; `status` is the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def error_reply(exc: Exception) -> Reply:
    if isinstance(exc, ApiError):
        return exc.status, {"error": exc.message}
    # Never echo an unexpected exception: it can hold paths or text.
    return HTTPStatus.INTERNAL_SERVER_ERROR.value, {"error": "the server could not do that"}


def need(params: Mapping[str, Any], name: str, limit: int = 2000) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"{name} is required")
    if len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ApiError(400, f"{name} is not valid")
    return value


def need_int(params: Mapping[str, Any], name: str, default: int, low: int, high: int) -> int:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise ApiError(400, f"{name} must be between {low} and {high}")
    return value
