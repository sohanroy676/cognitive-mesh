"""The web UI's access rules (ADR-0031): token, Host check, GET only, headers, static allowlist."""

import re
from pathlib import Path

import pytest

from cognitive_mesh.ui import server as ui_server
from cognitive_mesh.ui.config import UiConfig
from ui_support import TOKEN, WRONG_TOKEN, World, serving

STATIC_DIR = Path(ui_server.__file__).parent / "static"


def test_every_api_route_needs_the_token(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        assert c.get("/api/status", token=None).status == 403
        assert c.get("/api/status", token=WRONG_TOKEN).status == 403
        assert c.get("/api/status", token="").status == 403
        assert c.get("/", token=None).status == 403
        assert c.get("/app.js", token=None).status == 403
        assert c.get("/api/graph.html", token=None).status == 403
        assert c.get("/api/status", token=None, headers={"X-Cogmesh-Token": TOKEN}).status == 200
        assert c.get("/api/status").status == 200
        assert c.get("/api/status", headers={"Cookie": "cogmesh_token=%%%;;;"}).status == 403


def test_the_url_token_starts_a_session_and_is_then_dropped_from_the_url(
    ui_world: World,
) -> None:
    with serving(ui_world.config()) as c:
        ok = c.get(f"/?token={TOKEN}", token=None)
        assert ok.status == 302 and ok.headers["location"] == "/"
        cookie = ok.headers["set-cookie"]
        assert f"cogmesh_token={TOKEN}" in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert c.get("/?token=nope", token=None).status == 403
        # a token on an API path is not accepted: only the cookie or the header is
        assert c.get(f"/api/status?token={TOKEN}", token=None).status == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:1", "127.0.0.1", "localhost", ""])
def test_a_foreign_host_header_is_refused_even_with_the_token(ui_world: World, host: str) -> None:
    with serving(ui_world.config()) as c:
        assert c.get("/api/status", host=host).status == 403
        assert c.get("/", host=host).status == 403


def test_localhost_and_the_loopback_address_are_both_accepted(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        port = c.server.port
        assert c.get("/api/status", host=f"localhost:{port}").status == 200
        assert c.get("/api/status", host=f"127.0.0.1:{port}").status == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_only_get_is_served(ui_world: World, method: str) -> None:
    with serving(ui_world.config()) as c:
        reply = c.request("/api/status", method=method)
        assert reply.status == 405 and reply.headers["allow"] == "GET"
        assert c.request("/api/status", method=method, host="evil.example").status == 403


@pytest.mark.parametrize(
    "path",
    [
        "/static/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/..%2fpyproject.toml",
        "/app.js/../../../pyproject.toml",
        "/index.html",
        "/app.js/",
        "/server.py",
        "//etc/passwd",
        "/api/../app.js",
        "/api/nope",
    ],
)
def test_a_path_is_never_turned_into_a_file_name(ui_world: World, path: str) -> None:
    with serving(ui_world.config()) as c:
        reply = c.get(path)
        assert reply.status == 404
        assert b"[project]" not in reply.body


def test_overlong_request_lines_are_refused(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        assert c.get("/api/search?q=" + "a" * 5000).status in (414, 400)


@pytest.mark.parametrize("path", ["/api/status", "/", "/app.js", "/nope", "/api/graph.html"])
def test_every_response_carries_the_security_headers(ui_world: World, path: str) -> None:
    with serving(ui_world.config()) as c:
        for reply in (c.get(path), c.get(path, token=None), c.request(path, method="POST")):
            h = reply.headers
            assert (
                "content-security-policy" in h
                and "frame-ancestors 'self'" in h["content-security-policy"]
            )
            assert h["x-content-type-options"] == "nosniff"
            assert h["referrer-policy"] == "no-referrer"
            assert h["cache-control"] == "no-store"
            assert "access-control-allow-origin" not in h
            assert "server" not in h or "Python" not in h["server"]


def test_the_page_policy_allows_no_inline_script_and_no_remote_origin(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        csp = c.get("/").headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp
        assert "unsafe-inline" not in csp.split("style-src")[0] and "http" not in csp
        graph = c.get("/api/graph.html").headers["content-security-policy"]
        assert "script" not in graph and "default-src 'none'" in graph


def test_the_server_binds_the_loopback_address_only(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        assert c.server.server_address[0] == "127.0.0.1"
    assert not hasattr(UiConfig, "host") and "host" not in UiConfig.model_fields


def test_internal_errors_are_opaque(ui_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: object, params: object) -> None:
        raise RuntimeError("secret detail at C:\\Users\\someone\\file")

    monkeypatch.setattr("cognitive_mesh.ui.api.UiApi.status", boom)
    with serving(ui_world.config()) as c:
        reply = c.get("/api/status")
        assert reply.status == 500 and reply.json() == {"error": "internal error"}
        assert b"secret" not in reply.body and b"Traceback" not in reply.body


@pytest.mark.parametrize("name", ["app.js", "graph.js"])
def test_the_scripts_never_parse_markup_evaluate_text_or_set_styles(name: str) -> None:
    script = (STATIC_DIR / name).read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        'setTimeout("',
        "srcdoc",
        ".style",
        "cssText",
        'setAttribute("style"',
        "createContextualFragment",
        "DOMParser",
    ):
        assert banned not in script, banned
    # Element.append(null) writes the word "null" into the page: appends go through put()/reput()
    unsafe = re.findall(
        r"\b(?:root|out|list|inspector|pinList)\.(?:append|replaceChildren)\(", script
    )
    assert not unsafe, unsafe


def test_the_page_loads_only_scripts_from_its_own_origin() -> None:
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "onclick" not in page and "<style" not in page and ' style="' not in page
    sources = re.findall(r"<script([^>]*)>", page)
    assert sources and all(re.fullmatch(r'\s*src="/[a-z]+\.js"\s*', a) for a in sources)
    assert page.count("<script") == len(sources)


def test_static_files_are_served_with_their_types(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        assert c.get("/").headers["content-type"].startswith("text/html")
        assert c.get("/app.js").headers["content-type"].startswith("text/javascript")
        assert c.get("/graph.js").headers["content-type"].startswith("text/javascript")
        assert c.get("/app.css").headers["content-type"].startswith("text/css")
        assert b"Cognitive Mesh" in c.get("/").body
