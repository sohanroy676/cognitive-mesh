"""Pushing a change branch and opening a pull request (ADR-0042): no real GitHub is contacted."""

import base64
import http.server
import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.github import (
    PublishError,
    PublishFailure,
    create_pull_request,
    parse_origin,
    pr_title,
    push_branch,
)
from cognitive_mesh.change.worktree import create_worktree
from cognitive_mesh.providers.http import HttpReply, TransportError
from cognitive_mesh.workspace.fetch import GitResult, run_git_with
from impact_support import commit_all, git

CID = "ab12cd34ef56"
FAKE = "tok" + "en-value-for-tests-0123456789"  # built so no secret-shaped literal is in the file
TOKEN = SecretStr(FAKE)


def make_repo(tmp: Path) -> tuple[Path, Path, str]:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "init", "-q")
    commit_all(repo, "base")
    wt = create_worktree(repo, tmp / "wts", CID)
    (wt.toplevel / "a.py").write_text("x = 2\n", encoding="utf-8")
    git(wt.toplevel, "commit", "-aqm", "change")
    bare = tmp / "remote.git"
    git(tmp, "init", "-q", "--bare", str(bare))
    return repo, bare, wt.branch


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> GitResult:
        self.calls.append((list(argv), dict(env)))
        return run_git_with(argv, cwd, env_extra=env)


def test_a_push_reaches_the_remote_and_the_token_is_only_in_the_environment(tmp_path: Path) -> None:
    repo, bare, branch = make_repo(tmp_path)
    runner = Recorder()
    push_branch(repo, "o", "r", branch, TOKEN, runner=runner, remote=str(bare))
    assert git(bare, "rev-parse", f"refs/heads/{branch}") == git(repo, "rev-parse", branch)
    ((argv, env),) = runner.calls
    assert FAKE not in " ".join(argv)
    assert "--force" not in argv and "-f" not in argv
    assert not any(a.startswith("+") for a in argv)
    header = env["GIT_CONFIG_VALUE_0"]
    assert base64.b64decode(header.split()[-1]).decode() == f"x-access-token:{FAKE}"
    assert "core.hooksPath" in " ".join(argv) and "credential.helper=" in argv


def test_a_second_push_with_other_history_is_rejected_not_forced(tmp_path: Path) -> None:
    repo, bare, branch = make_repo(tmp_path)
    push_branch(repo, "o", "r", branch, TOKEN, remote=str(bare))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    git(other, "checkout", "-q", branch)
    (other / "a.py").write_text("x = 99\n", encoding="utf-8")
    git(other, "commit", "-aqm", "elsewhere")
    git(other, "push", "-q", "origin", branch)
    wt_dir = tmp_path / "wts" / f"change-{CID}"
    (wt_dir / "a.py").write_text("x = 3\n", encoding="utf-8")
    git(wt_dir, "commit", "-aqm", "local")
    with pytest.raises(PublishError) as info:
        push_branch(repo, "o", "r", branch, TOKEN, remote=str(bare))
    assert info.value.failure is PublishFailure.REJECTED
    assert FAKE not in str(info.value)


def test_no_token_and_foreign_branches_are_refused_before_git_runs(tmp_path: Path) -> None:
    repo, bare, branch = make_repo(tmp_path)
    runner = Recorder()
    for token in (None, SecretStr("  ")):
        with pytest.raises(PublishError) as info:
            push_branch(repo, "o", "r", branch, token, runner=runner, remote=str(bare))
        assert info.value.failure is PublishFailure.NOT_CONFIGURED
    with pytest.raises(ChangeError, match="cogmesh/change"):
        push_branch(repo, "o", "r", "main", TOKEN, runner=runner, remote=str(bare))
    assert runner.calls == []


@pytest.mark.parametrize(
    ("stderr", "failure"),
    [
        ("remote: Invalid username or password.\nfatal: Authentication failed for 'x'", "auth"),
        ("remote: Repository not found.", "not_found"),
        ("fatal: unable to access: Could not resolve host: github.com", "offline"),
        ("! [rejected] a -> a (non-fast-forward)", "rejected"),
        ("something strange", "other"),
    ],
)
def test_push_failures_map_to_fixed_kinds_and_never_repeat_git_output(
    tmp_path: Path, stderr: str, failure: str
) -> None:
    def fake(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> GitResult:
        return GitResult(1, "", stderr)

    with pytest.raises(PublishError) as info:
        push_branch(tmp_path, "o", "r", f"cogmesh/change-{CID}", TOKEN, runner=fake)
    assert info.value.failure.value == failure
    assert stderr.split(":")[0] not in str(info.value) or failure == "other"
    assert "x-access-token" not in str(info.value) and FAKE not in str(info.value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/octo/demo.git", ("octo", "demo")),
        ("https://github.com/octo/demo", ("octo", "demo")),
        ("git@github.com:octo/demo.git", ("octo", "demo")),
        ("ssh://git@github.com/octo/demo", ("octo", "demo")),
    ],
)
def test_origin_addresses(url: str, expected: tuple[str, str]) -> None:
    assert parse_origin(url) == expected


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/o/r", "http://github.com/o/r", "git@evil.com:o/r", "/local/path", ""],
)
def test_other_origins_are_refused(url: str) -> None:
    with pytest.raises(PublishError) as info:
        parse_origin(url)
    assert info.value.failure is PublishFailure.NOT_GITHUB


class FakeApi:
    def __init__(self, status: int = 201, body: object = None, error: str | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "html_url": "https://github.com/octo/demo/pull/7",
                "number": 7,
            }
        )
        self.error = error
        self.seen: list[tuple[str, bytes, Mapping[str, str]]] = []

    def post_json(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpReply:
        self.seen.append((url, body, dict(headers)))
        if self.error:
            raise TransportError(self.error)  # type: ignore[arg-type]
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return HttpReply(self.status, raw)


def open_pr(api: FakeApi, token: SecretStr | None = TOKEN) -> object:
    return create_pull_request(
        "octo", "demo", "cogmesh/change-x", "main", "change: t", "body", token, transport=api
    )


def test_a_pull_request_is_opened_with_the_token_only_in_the_header() -> None:
    api = FakeApi()
    got = open_pr(api)
    assert (got.url, got.number) == ("https://github.com/octo/demo/pull/7", 7)  # type: ignore[attr-defined]
    ((url, body, headers),) = api.seen
    assert url == "https://api.github.com/repos/octo/demo/pulls"
    assert headers["Authorization"] == f"Bearer {FAKE}"
    assert FAKE.encode() not in body
    assert json.loads(body) == {
        "title": "change: t",
        "head": "cogmesh/change-x",
        "base": "main",
        "body": "body",
    }


@pytest.mark.parametrize(
    ("status", "body", "failure"),
    [
        (401, {"message": "Bad credentials"}, "auth"),
        (403, {"message": "forbidden"}, "auth"),
        (403, {"message": "API rate limit exceeded"}, "rate_limited"),
        (404, {"message": "Not Found"}, "not_found"),
        (422, {"message": "A pull request already exists"}, "unprocessable"),
        (429, {}, "rate_limited"),
        (500, {}, "other"),
    ],
)
def test_api_failures_map_to_fixed_kinds_and_never_repeat_the_response(
    status: int, body: object, failure: str
) -> None:
    with pytest.raises(PublishError) as info:
        open_pr(FakeApi(status, body))
    assert info.value.failure.value == failure
    assert "already exists" not in str(info.value) and "Bad credentials" not in str(info.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        {"number": 1},
        {"html_url": "https://evil.example/x", "number": 1},
        {"html_url": "https://github.com/octo/demo/pull/1", "number": "one"},
    ],
)
def test_a_reply_that_is_not_a_pull_request_is_refused(body: object) -> None:
    with pytest.raises(PublishError) as info:
        open_pr(FakeApi(201, body))
    assert info.value.failure is PublishFailure.BAD_RESPONSE


def test_transport_errors_and_a_missing_token() -> None:
    with pytest.raises(PublishError) as timeout:
        open_pr(FakeApi(error="timeout"))
    assert timeout.value.failure is PublishFailure.TIMEOUT
    with pytest.raises(PublishError) as offline:
        open_pr(FakeApi(error="unavailable"))
    assert offline.value.failure is PublishFailure.OFFLINE
    api = FakeApi()
    with pytest.raises(PublishError) as missing:
        open_pr(api, None)
    assert missing.value.failure is PublishFailure.NOT_CONFIGURED and api.seen == []


@pytest.fixture
def stub_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    seen: list[dict[str, object]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            seen.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            reply = json.dumps({"html_url": "http://x/pull/3", "number": 3}).encode()
            self.send_response(201)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def test_the_real_transport_talks_to_a_local_stub(
    stub_server: tuple[str, list[dict[str, object]]],
) -> None:
    base, seen = stub_server
    got = create_pull_request(
        "octo", "demo", "cogmesh/change-x", "main", "t", "b", TOKEN, api_base=base
    )
    assert got.number == 3
    assert seen[0]["path"] == "/repos/octo/demo/pulls" and seen[0]["auth"] == f"Bearer {FAKE}"


def test_titles_are_one_short_redacted_line() -> None:
    assert pr_title("a\nb  c") == "change: a b c"
    assert len(pr_title("x" * 500)) <= len("change: ") + 72
    assert pr_title("   ") == "change: small change"
