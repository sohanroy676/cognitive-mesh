"""Create the sample `shop-api` project as a git repository, ready to add in the chat.

    uv run python demo/make_shop_api.py DEST [--origin https://github.com/OWNER/REPO.git]

DEST must not exist yet. The repository gets one commit on branch `main`. Nothing is pushed: with
`--origin`, the remote is only added, and the command to publish `main` is printed for you to run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cognitive_mesh.serve.replay import ReplayError, make_git_sample

SOURCE = Path(__file__).parent / "shop-api"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", type=Path)
    parser.add_argument("--origin", default=None, help="GitHub URL to add as the origin remote")
    args = parser.parse_args()
    try:
        make_git_sample(SOURCE, args.dest, args.origin)
    except ReplayError as exc:
        raise SystemExit(f"{exc}; choose a folder that does not exist") from None
    print(f"created {args.dest} (git, branch main, one commit)")
    if args.origin:
        print(
            f"origin set to {args.origin}; publish main yourself with: "
            f"git -C {args.dest} push -u origin main"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
