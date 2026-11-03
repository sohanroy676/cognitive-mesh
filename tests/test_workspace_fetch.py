"""Fetching public GitHub repositories with git (ADR-0036): fake runner and a real local repo."""

import os
import shutil
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cognitive_mesh.workspace.fetch import (
    FetchError,
    FetchFailure,
    GitResult,
    checkout_dir,
    environment_note,
    fetch_github,
    normalise_github_url,
    parse_github_url,
)
from cognitive_mesh.workspace.models import WorkspaceError

SHA = "b" * 40


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r",
        "https://evil.example/o/r",
        "https://github.com/o/r/extra",
        "https://github.com/o/r?x=1",
        "https://user:pw@github.com/o/r",
        "https://github.com:444/o/r",
        "https://github.com/../r",
        "https://github.com/o/r;rm -rf",
        "https://github.com/o/r with space",
        "git@github.com:o/r.git",
        "file:///etc",
    ],
)
def test_unsupported_urls_are_refused(url: str) -> None:
    with pytest.raises(WorkspaceError, match="only https://github.com"):
        parse_github_url(url)


def test_url_is_normalised() -> None:
    assert normalise_github_url("https://github.com/Own-er/re.po.git") == (
        "https://github.com/Own-er/re.po"
    )


def test_checkout_dir_needs_a_full_sha() -> None:
    with pytest.raises(WorkspaceError, match="full commit"):
        checkout_dir(Path("c"), "https://github.com/o/r", "abc123")


class Fake:
    """Scripted runner: records argv, returns the next result per git subcommand."""

    def __init__(self, results: dict[str, GitResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], cwd: Path | None) -> GitResult:
        self.calls.append(list(argv))
        for verb in ("clone", "rev-parse", "checkout"):
            if verb in argv:
                result = self.results.get(verb, GitResult(0, "", ""))
                if verb == "clone" and result.returncode == 0:
                    Path(argv[-1]).mkdir(parents=True)
                    (Path(argv[-1]) / "f.txt").write_text("x", encoding="utf-8")
                return result
        return GitResult(0, "", "")


@pytest.mark.parametrize(
    ("stderr", "failure"),
    [
        ("fatal: unable to access: Could not resolve host: github.com", FetchFailure.OFFLINE),
        (
            "fatal: could not read Username: terminal prompts disabled",
            FetchFailure.NOT_FOUND_OR_PRIVATE,
        ),
        ("remote: Repository not found.", FetchFailure.NOT_FOUND_OR_PRIVATE),
        ("something odd https://user:SECRET@host", FetchFailure.OTHER),
    ],
)
def test_clone_failures_map_to_fixed_kinds_and_never_repeat_git_output(
    tmp_path: Path, stderr: str, failure: FetchFailure
) -> None:
    fake = Fake({"clone": GitResult(128, "", stderr)})
    with pytest.raises(FetchError) as info:
        fetch_github("https://github.com/o/r", tmp_path / "c", runner=fake)
    assert info.value.failure is failure
    assert "SECRET" not in str(info.value) and "github.com" not in str(info.value)
    assert not list((tmp_path / "c").iterdir())  # scratch removed


def test_missing_commit_is_moved_commit_and_nothing_else_is_substituted(tmp_path: Path) -> None:
    fake = Fake({"checkout": GitResult(1, "", "fatal: reference is not a tree")})
    with pytest.raises(FetchError) as info:
        fetch_github("https://github.com/o/r", tmp_path / "c", SHA, runner=fake)
    assert info.value.failure is FetchFailure.MOVED_COMMIT
    assert not any("rev-parse" in c for c in fake.calls)


def test_safe_flags_and_read_only_tree(tmp_path: Path) -> None:
    fake = Fake({"rev-parse": GitResult(0, SHA + "\n", "")})
    path, commit = fetch_github("https://github.com/o/r", tmp_path / "c", runner=fake)
    assert commit == SHA and path == checkout_dir(tmp_path / "c", "https://github.com/o/r", SHA)
    clone = next(c for c in fake.calls if "clone" in c)
    assert "protocol.file.allow=never" in clone and "credential.helper=" in clone
    assert any(a.startswith("core.hooksPath=") for a in clone)
    assert "--no-checkout" in clone
    assert (
        not os.access(path / "f.txt", os.W_OK) or not (path / "f.txt").stat().st_mode & stat.S_IWUSR
    )


def test_existing_checkout_is_reused_without_running_git(tmp_path: Path) -> None:
    target = checkout_dir(tmp_path / "c", "https://github.com/o/r", SHA)
    target.mkdir(parents=True)
    fake = Fake({})
    assert fetch_github("https://github.com/o/r", tmp_path / "c", SHA, runner=fake)[0] == target
    assert fake.calls == []


def test_git_environment_has_no_prompts_or_user_config() -> None:
    env = environment_note()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "GITHUB_TOKEN" not in env and "GROQ_API_KEY" not in env


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        [
            shutil.which("git") or "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@needs_git
def test_real_git_clone_pins_a_commit_and_survives_history_moving_on(tmp_path: Path) -> None:
    repo = tmp_path / "remote"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "pyproject.toml").write_text('[project]\nname = "r"\n', encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    first = git(repo, "rev-parse", "HEAD")
    cache = tmp_path / "cache"
    url = "https://github.com/o/r"
    path, commit = fetch_github(url, cache, remote=str(repo), allow_file_remote=True)
    assert commit == first and (path / "pyproject.toml").is_file()
    assert not (path / "pyproject.toml").stat().st_mode & stat.S_IWUSR
    (repo / "later.txt").write_text("x", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "two")
    again, same = fetch_github(url, cache, first, remote=str(repo), allow_file_remote=True)
    assert (again, same) == (path, first) and not (again / "later.txt").exists()


@needs_git
def test_real_git_unknown_commit_is_moved_commit_and_file_remote_needs_the_test_flag(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "remote"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    with pytest.raises(FetchError) as info:
        fetch_github(
            "https://github.com/o/r",
            tmp_path / "c",
            "c" * 40,
            remote=str(repo),
            allow_file_remote=True,
        )
    assert info.value.failure is FetchFailure.MOVED_COMMIT
    with pytest.raises(FetchError):  # the file protocol is off unless a test turns it on
        fetch_github("https://github.com/o/r", tmp_path / "c2", remote=str(repo))
