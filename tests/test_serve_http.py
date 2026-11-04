"""Access rules of the three local servers (ADR-0044): token, Host, Origin, type, methods."""

from pathlib import Path

from serve_support import TOKEN, call, config_for, running


def test_no_token_means_forbidden_on_every_server(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        for server in (s.app, s.graph, s.data):
            assert call(server, "GET", "/").status == 403
            assert call(server, "GET", "/api/meta").status == 403
            assert call(server, "GET", "/api/meta", token="wrong").status == 403
            assert call(server, "GET", "/api/meta", token=TOKEN).status == 200


def test_token_in_url_starts_a_session_with_a_per_server_cookie(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        names = {}
        for key, server in (("app", s.app), ("graph", s.graph), ("data", s.data)):
            r = call(server, "GET", f"/?token={TOKEN}")
            assert r.status == 302 and r.headers["location"] == "/"
            cookie = r.headers["set-cookie"]
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
            names[key] = cookie.split("=")[0]
            ok = call(server, "GET", "/", headers={"Cookie": cookie.split(";")[0]})
            assert ok.status == 200 and b"<title>" in ok.raw
        assert len(set(names.values())) == 3  # cookies ignore ports, so the names differ


def test_a_cookie_from_another_server_does_not_open_this_one(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        cookie = call(s.app, "GET", f"/?token={TOKEN}").headers["set-cookie"].split(";")[0]
        assert call(s.graph, "GET", "/api/workspace", headers={"Cookie": cookie}).status == 403


def test_wrong_host_is_refused(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        r = call(s.app, "GET", "/api/meta", token=TOKEN, headers={"Host": "evil.example"})
        assert r.status == 403


def test_responses_carry_the_security_headers(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        r = call(s.app, "GET", "/api/meta", token=TOKEN)
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_read_only_servers_refuse_every_write(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        for server in (s.graph, s.data):
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                r = call(server, method, "/api/meta", token=TOKEN, body={})
                assert r.status == 405, (server.port, method)


def test_unknown_methods_on_the_chat_server_are_refused(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        for method in ("PUT", "DELETE", "PATCH"):
            assert call(s.app, method, "/api/chat/send", token=TOKEN, body={}).status == 405


def test_post_needs_origin_token_header_json_and_a_small_object(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        path = "/api/projects/confirm"
        body = {"project": "nothing"}
        # no Origin, wrong Origin
        assert call(s.app, "POST", path, token=TOKEN, body=body, origin=False).status == 403
        evil = call(
            s.app, "POST", path, token=TOKEN, body=body, headers={"Origin": "http://evil.test"}
        )
        assert evil.status == 403
        # a cookie alone is not enough for a write
        cookie = call(s.app, "GET", f"/?token={TOKEN}").headers["set-cookie"].split(";")[0]
        assert call(s.app, "POST", path, body=body, headers={"Cookie": cookie}).status == 403
        assert call(s.app, "POST", path, token="wrong", body=body).status == 403
        # content type, JSON, object, size
        assert call(s.app, "POST", path, token=TOKEN, body=body, json_type=False).status == 415
        assert call(s.app, "POST", path, token=TOKEN, raw=b"{not json").status == 400
        assert call(s.app, "POST", path, token=TOKEN, raw=b"[1, 2]").status == 400
        big = b'{"project": "' + b"x" * 70_000 + b'"}'
        assert call(s.app, "POST", path, token=TOKEN, raw=big).status == 413
        # the request that passes every check reaches the handler, which refuses on its own terms
        r = call(s.app, "POST", path, token=TOKEN, body=body)
        assert r.status == 409 and "add projects first" in r.json["error"]


def test_post_to_an_unknown_path_is_not_found_and_unknown_fields_are_refused(
    tmp_path: Path,
) -> None:
    with running(config_for(tmp_path)) as s:
        assert call(s.app, "POST", "/api/nope", token=TOKEN, body={}).status == 404
        r = call(s.app, "POST", "/api/projects/add", token=TOKEN, body={"source": "x", "extra": 1})
        assert r.status == 400 and "extra" in r.json["error"]


def test_meta_lists_the_real_ports(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        meta = call(s.graph, "GET", "/api/meta", token=TOKEN).json
        assert meta["ports"] == {"app": s.app.port, "graph": s.graph.port, "data": s.data.port}
        assert meta["token"] == TOKEN
