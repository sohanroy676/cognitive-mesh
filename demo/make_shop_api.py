"""Create the sample `shop-api` project as a git repository, ready to add in the chat.

    uv run python demo/make_shop_api.py DEST [--origin https://github.com/OWNER/REPO.git]

DEST must not exist yet. The repository gets one commit on branch `main`. Nothing is pushed: with
`--origin`, the remote is only added, and the command to publish `main` is printed for you to run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # noqa: S404 - runs git with fixed arguments
import sys
from pathlib import Path

SOURCE = Path(__file__).parent / "shop-api"


def git(dest: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [  # noqa: S607
            "git",
            "-c",
            "user.name=Cognitive Mesh demo",
            "-c",
            "user.email=demo@example.invalid",
            *args,
        ],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )


def make(dest: Path, origin: str | None = None) -> None:
    if dest.exists():
        raise SystemExit(f"{dest} already exists; choose a folder that does not")
    shutil.copytree(SOURCE, dest)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "Initial shop-api")
    if origin is not None:
        git(dest, "remote", "add", "origin", origin)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", type=Path)
    parser.add_argument("--origin", default=None, help="GitHub URL to add as the origin remote")
    args = parser.parse_args()
    make(args.dest, args.origin)
    print(f"created {args.dest} (git, branch main, one commit)")
    if args.origin:
        print(
            f"origin set to {args.origin}; publish main yourself with: "
            f"git -C {args.dest} push -u origin main"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
