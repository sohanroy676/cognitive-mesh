"""Push a change branch and open a pull request (ADR-0042). Nothing is merged here.

The token is passed to git through environment variables (never argv) and to the API in a header; it
is never printed, stored or put into an error. Git's own output and the API's response text are not
repeated: every failure maps to a fixed kind and message. A push is never forced.
"""

import base64
import json
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.ingestion.secrets import redact_secrets
from cognitive_mesh.providers.http import HttpReply, HttpTransport, StdlibTransport, TransportError
from cognitive_mesh.workspace.fetch import FetchError, GitResult, parse_github_url, run_git_with

API_BASE: Final = "https://api.github.com"
PUSH_TIMEOUT_S: Final = 300
API_TIMEOUT_S: Final = 30.0
MAX_API_BYTES: Final = 1024 * 1024
MAX_TITLE: Final = 72
_SSH = re.compile(r"(?:ssh://)?git@github\.com[:/]([A-Za-z0-9._-]{1,100})/([A-Za-z0-9._-]{1,100})")
_BRANCH = re.compile(r"cogmesh/change-[0-9a-f]{12,32}")


class PublishFailure(StrEnum):
    NOT_CONFIGURED = "not_configured"
    NOT_GITHUB = "not_github"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"
    OFFLINE = "offline"
    UNPROCESSABLE = "unprocessable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    BAD_RESPONSE = "bad_response"
    OTHER = "other"


_MESSAGES: Final = {
    PublishFailure.NOT_CONFIGURED: "GITHUB_TOKEN is not set (environment or the ignored .env)",
    PublishFailure.NOT_GITHUB: "the origin remote is not a github.com repository",
    PublishFailure.AUTH: "GitHub refused the token (missing, expired or without access)",
    PublishFailure.NOT_FOUND: "GitHub does not know the repository, or the token cannot see it",
    PublishFailure.REJECTED: "GitHub rejected the push (the branch exists with other history?)",
    PublishFailure.OFFLINE: "cannot reach github.com",
    PublishFailure.UNPROCESSABLE: (
        "GitHub refused the pull request (one may already exist, or the branch is not pushed)"
    ),
    PublishFailure.RATE_LIMITED: "GitHub is rate limiting this token; try again later",
    PublishFailure.TIMEOUT: "GitHub did not answer in time",
    PublishFailure.BAD_RESPONSE: "GitHub's answer could not be understood",
    PublishFailure.OTHER: "publishing failed",
}


class PublishError(ChangeError):
    def __init__(self, failure: PublishFailure) -> None:
        super().__init__(_MESSAGES[failure])
        self.failure = failure


PushRunner = Callable[[Sequence[str], Path, Mapping[str, str]], GitResult]


def _default_runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> GitResult:
    try:
        return run_git_with(argv, cwd, env_extra=env, timeout_s=PUSH_TIMEOUT_S)
    except FetchError:
        raise PublishError(PublishFailure.TIMEOUT) from None


def parse_origin(url: str) -> tuple[str, str]:
    """(owner, repo) of a github.com https or ssh address, else PublishError(NOT_GITHUB)."""
    text = url.strip()
    match = _SSH.fullmatch(text.removesuffix(".git"))
    if match:
        return match.group(1), match.group(2)
    try:
        return parse_github_url(text)
    except Exception:  # noqa: BLE001 - any parse problem is "not a github address"
        raise PublishError(PublishFailure.NOT_GITHUB) from None


def origin_of(top: Path, runner: PushRunner = _default_runner) -> tuple[str, str]:
    done = runner(["remote", "get-url", "origin"], top, {})
    if done.returncode != 0:
        raise PublishError(PublishFailure.NOT_GITHUB)
    return parse_origin(done.stdout)


def _classify_push(result: GitResult) -> PublishFailure:
    text = result.stderr.lower()
    if any(m in text for m in ("rejected", "non-fast-forward", "fetch first")):
        return PublishFailure.REJECTED
    if any(m in text for m in ("authentication failed", "invalid username", "403", "401")):
        return PublishFailure.AUTH
    if any(m in text for m in ("repository not found", "not found")):
        return PublishFailure.NOT_FOUND
    if any(m in text for m in ("could not resolve host", "failed to connect", "timed out")):
        return PublishFailure.OFFLINE
    return PublishFailure.OTHER


def push_branch(
    top: Path,
    owner: str,
    repo: str,
    branch: str,
    token: SecretStr | None,
    *,
    runner: PushRunner = _default_runner,
    remote: str | None = None,
) -> None:
    """Push `branch` to github.com/owner/repo as the same branch name. Never forced.

    `remote` replaces the address and exists for tests with a local bare repository only.
    """
    if token is None or not token.get_secret_value().strip():
        raise PublishError(PublishFailure.NOT_CONFIGURED)
    if not _BRANCH.fullmatch(branch):
        raise ChangeError("only a cogmesh/change-ID branch is pushed")
    basic = base64.b64encode(f"x-access-token:{token.get_secret_value()}".encode()).decode()
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }
    target = remote or f"https://github.com/{owner}/{repo}.git"
    with tempfile.TemporaryDirectory(prefix="cogmesh-nohooks-") as hooks:
        done = runner(
            [
                "-c",
                f"core.hooksPath={hooks}",
                "-c",
                "credential.helper=",
                "-c",
                f"protocol.file.allow={'always' if remote else 'never'}",
                "push",
                "--no-verify",
                target,
                f"refs/heads/{branch}:refs/heads/{branch}",
            ],
            top,
            env,
        )
    if done.returncode != 0:
        raise PublishError(_classify_push(done))


@dataclass(frozen=True)
class PullRequest:
    url: str
    number: int


def pr_title(summary: str) -> str:
    one_line = " ".join(summary.split())
    clean, _ = redact_secrets("".join(ch for ch in one_line if ch.isprintable()))
    return "change: " + (clean[:MAX_TITLE] or "small change")


def _api_failure(reply: HttpReply) -> PublishFailure:
    if reply.status in (401, 403):
        return (
            PublishFailure.RATE_LIMITED
            if reply.status == 403 and b"rate limit" in reply.body.lower()
            else PublishFailure.AUTH
        )
    if reply.status == 404:
        return PublishFailure.NOT_FOUND
    if reply.status == 422:
        return PublishFailure.UNPROCESSABLE
    if reply.status == 429:
        return PublishFailure.RATE_LIMITED
    return PublishFailure.OTHER


def create_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    token: SecretStr | None,
    *,
    transport: HttpTransport | None = None,
    api_base: str = API_BASE,
) -> PullRequest:
    """Open a pull request from `head` into `base`. The answer's text is never repeated."""
    if token is None or not token.get_secret_value().strip():
        raise PublishError(PublishFailure.NOT_CONFIGURED)
    payload = json.dumps({"title": title, "head": head, "base": base, "body": body}).encode()
    headers = {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cognitive-mesh",
        "Content-Type": "application/json",
    }
    try:
        reply = (transport or StdlibTransport()).post_json(
            f"{api_base}/repos/{owner}/{repo}/pulls",
            payload,
            headers,
            timeout_s=API_TIMEOUT_S,
            max_body_bytes=MAX_API_BYTES,
        )
    except TransportError as exc:
        kind = PublishFailure.TIMEOUT if exc.kind == "timeout" else PublishFailure.OFFLINE
        raise PublishError(kind) from None
    if reply.status != 201:
        raise PublishError(_api_failure(reply))
    try:
        data = json.loads(reply.body)
        url = data["html_url"]
        number = data["number"]
    except (ValueError, KeyError, TypeError):
        raise PublishError(PublishFailure.BAD_RESPONSE) from None
    expected = f"https://github.com/{owner}/{repo}/pull/"
    if api_base == API_BASE and not (isinstance(url, str) and url.startswith(expected)):
        raise PublishError(PublishFailure.BAD_RESPONSE)
    if not isinstance(url, str) or not isinstance(number, int) or len(url) > 300:
        raise PublishError(PublishFailure.BAD_RESPONSE)
    return PullRequest(url=url, number=number)
