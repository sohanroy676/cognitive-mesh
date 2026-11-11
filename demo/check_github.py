"""Before a live pull-request demo: check that everything the push needs is in place.

    uv run python demo/check_github.py PROJECT_FOLDER [--online]

Without --online nothing leaves your computer: it checks the origin remote, the branch, and that
GITHUB_TOKEN and COGMESH_ALLOW_REMOTE_CONTENT are set (the token itself is never printed).
With --online it also asks api.github.com, with your token, whether the repository is visible,
whether the token may push to it, and whether its default branch exists.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess  # noqa: S404 - runs git with fixed arguments
import sys
import urllib.error
import urllib.request
from pathlib import Path

from cognitive_mesh.config import ConfigError, load_github_settings
from cognitive_mesh.workspace.fetch import FetchError, parse_github_url


def git_out(folder: Path, *args: str) -> str | None:
    done = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["git", *args],  # noqa: S607
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def api_get(path: str, token: str) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        "https://api.github.com" + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "cognitive-mesh-demo-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:  # noqa: S310
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except OSError:
        return 0, {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--online", action="store_true", help="also ask api.github.com")
    args = parser.parse_args()
    problems: list[str] = []

    def line(ok: bool, text: str) -> None:
        print(("ok      " if ok else "MISSING ") + text)
        if not ok:
            problems.append(text)

    folder = args.folder
    top = git_out(folder, "rev-parse", "--show-toplevel") if folder.is_dir() else None
    line(top is not None, "the folder is a git repository")
    owner = repo = None
    if top is not None:
        origin = git_out(folder, "remote", "get-url", "origin")
        line(origin is not None, "an origin remote exists")
        if origin:
            with contextlib.suppress(FetchError):
                owner, repo = parse_github_url(origin)
            line(
                owner is not None,
                "origin is a github.com repository" + (f" ({owner}/{repo})" if owner else ""),
            )
        branch = git_out(folder, "rev-parse", "--abbrev-ref", "HEAD")
        line(
            branch not in (None, "HEAD"),
            f"HEAD is on a branch ({branch}); a detached HEAD has no pull-request base",
        )
    try:
        settings = load_github_settings()
    except ConfigError as exc:
        print(f"MISSING the GitHub settings are invalid: {exc}")
        return 1
    token = settings.github_token.get_secret_value() if settings.github_token else ""
    line(bool(token.strip()), "GITHUB_TOKEN is set (environment or the ignored .env)")
    line(
        settings.allow_remote_content, "COGMESH_ALLOW_REMOTE_CONTENT=true (the diff goes to GitHub)"
    )
    if args.online and token.strip() and owner and repo:
        status, info = api_get(f"/repos/{owner}/{repo}", token)
        line(
            status == 200,
            f"GitHub shows the repository to this token (HTTP {status or 'no answer'})",
        )
        if status == 200:
            perms = info.get("permissions")
            line(isinstance(perms, dict) and bool(perms.get("push")), "the token may push to it")
            default = str(info.get("default_branch") or "")
            seen, _ = (
                api_get(f"/repos/{owner}/{repo}/branches/{default}", token) if default else (0, {})
            )
            line(
                seen == 200,
                f"the default branch '{default}' exists on GitHub "
                f"(publish it first: git push -u origin {default})",
            )
    print()
    print("Also: the project needs a declared check that passes, and the server must run with")
    print("--allow-unisolated-checks, otherwise 'Open a pull request' is refused (demo/README.md).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
