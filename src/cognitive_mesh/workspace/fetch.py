"""Fetch a public GitHub repository with the `git` CLI (ADR-0036). The only module that runs `git`.

Fixed argument lists, no shell, no prompts, no credentials, no hooks, a timeout on every call.
Failures map to fixed kinds and messages; git's own output is never repeated (it can hold URLs).
The checked-out tree is made read-only and is afterwards read only through ingestion.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from cognitive_mesh.workspace.models import WorkspaceError

TIMEOUT_S: Final = 300
_URL = re.compile(r"https://github\.com/([A-Za-z0-9._-]{1,100})/([A-Za-z0-9._-]{1,100})")
_SHA = re.compile(r"[0-9a-f]{40}")
_ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG")
_OFFLINE = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "unable to access",
)
_PRIVATE = (
    "repository not found",
    "authentication failed",
    "terminal prompts disabled",
    "not found",
)


class FetchFailure(StrEnum):
    GIT_MISSING = "git_missing"
    OFFLINE = "offline"
    NOT_FOUND_OR_PRIVATE = "not_found_or_private"
    MOVED_COMMIT = "moved_commit"
    TIMEOUT = "timeout"
    OTHER = "other"


_MESSAGES: Final = {
    FetchFailure.GIT_MISSING: "git is not installed or not on PATH",
    FetchFailure.OFFLINE: "cannot reach the repository host (offline?)",
    FetchFailure.NOT_FOUND_OR_PRIVATE: (
        "the repository does not exist or is private (only public repositories are supported)"
    ),
    FetchFailure.MOVED_COMMIT: (
        "the recorded commit is no longer available in the repository; it was not replaced"
    ),
    FetchFailure.TIMEOUT: "git did not finish in time",
    FetchFailure.OTHER: "git failed",
}


class FetchError(WorkspaceError):
    def __init__(self, failure: FetchFailure) -> None:
        super().__init__(_MESSAGES[failure])
        self.failure = failure


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


GitRunner = Callable[[Sequence[str], Path | None], GitResult]


def parse_github_url(url: str) -> tuple[str, str]:
    """Return (owner, repo) for `https://github.com/OWNER/REPO[.git]`, else raise."""
    text = url.strip()
    if text.endswith(".git"):
        text = text[:-4]
    match = _URL.fullmatch(text)
    if match is None or match.group(2) in {".", ".."} or match.group(1) in {".", ".."}:
        raise WorkspaceError("only https://github.com/OWNER/REPO addresses are supported")
    return match.group(1), match.group(2)


def normalise_github_url(url: str) -> str:
    owner, repo = parse_github_url(url)
    return f"https://github.com/{owner}/{repo}"


def looks_like_url(text: str) -> bool:
    return "://" in text


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GCM_INTERACTIVE="never",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_ASKPASS=os.devnull,
        SSH_ASKPASS=os.devnull,
    )
    return env


def run_git(argv: Sequence[str], cwd: Path | None) -> GitResult:
    """Run git with a fixed argument list. Raises FetchError for a missing git or a timeout."""
    git = shutil.which("git")
    if git is None:
        raise FetchError(FetchFailure.GIT_MISSING)
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell, validated inputs
            [git, *argv],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise FetchError(FetchFailure.TIMEOUT) from None
    except OSError:
        raise FetchError(FetchFailure.OTHER) from None
    return GitResult(done.returncode, done.stdout, done.stderr)


def _classify(result: GitResult) -> FetchFailure:
    text = result.stderr.lower()
    if any(marker in text for marker in _OFFLINE):
        return FetchFailure.OFFLINE
    if any(marker in text for marker in _PRIVATE):
        return FetchFailure.NOT_FOUND_OR_PRIVATE
    return FetchFailure.OTHER


def checkout_dir(cache: Path, url: str, commit: str) -> Path:
    owner, repo = parse_github_url(url)
    if not _SHA.fullmatch(commit):
        raise WorkspaceError("the recorded commit is not a full commit id")
    return cache / f"{owner}__{repo}__{commit}"


def _make_read_only(tree: Path) -> None:
    for current, dirs, files in os.walk(tree):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            path = Path(current, name)
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def fetch_github(
    url: str,
    cache: Path,
    commit: str | None = None,
    *,
    runner: GitRunner = run_git,
    remote: str | None = None,
    allow_file_remote: bool = False,
) -> tuple[Path, str]:
    """Clone `url` into the cache and check out `commit` (default: the remote's HEAD), detached.

    Returns (directory, full commit id). An existing directory for that commit is reused. If the
    given commit is not in the repository the result is `moved_commit`; a newer commit is never
    substituted. `remote` and `allow_file_remote` exist for tests with a local repository only.
    """
    clean_url = normalise_github_url(url)
    if commit is not None and not _SHA.fullmatch(commit):
        raise WorkspaceError("the recorded commit is not a full commit id")
    if commit is not None:
        existing = checkout_dir(cache, clean_url, commit)
        if existing.is_dir():
            return existing, commit
    cache.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="clone-", dir=cache))
    hooks = scratch / "no-hooks"
    hooks.mkdir()
    target = scratch / "work"
    try:
        base = [
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "credential.helper=",
            "-c",
            f"protocol.file.allow={'always' if allow_file_remote else 'never'}",
        ]
        cloned = runner(
            [
                *base,
                "clone",
                "--no-checkout",
                "--filter=blob:none",
                remote or clean_url,
                str(target),
            ],
            None,
        )
        if cloned.returncode != 0:
            raise FetchError(_classify(cloned))
        if commit is None:
            head = runner([*base, "rev-parse", "HEAD"], target)
            found = head.stdout.strip()
            if head.returncode != 0 or not _SHA.fullmatch(found):
                raise FetchError(FetchFailure.OTHER)
            commit = found
        done = runner([*base, "checkout", "--quiet", "--detach", commit], target)
        if done.returncode != 0:
            raise FetchError(FetchFailure.MOVED_COMMIT)
        _make_read_only(target)
        final = checkout_dir(cache, clean_url, commit)
        if final.is_dir():
            return final, commit
        target.rename(final)
        return final, commit
    finally:
        shutil.rmtree(scratch, ignore_errors=True, onexc=_force_remove)


def _force_remove(function: Callable[[str], object], path: str, _exc: BaseException) -> None:
    """rmtree helper: read-only files (ours, on purpose) must still be removable from scratch."""
    try:
        os.chmod(path, stat.S_IWRITE)  # noqa: PTH101
        function(path)
    except OSError:
        pass


def environment_note() -> Mapping[str, str]:
    """For tests: the environment git would receive."""
    return _git_env()
