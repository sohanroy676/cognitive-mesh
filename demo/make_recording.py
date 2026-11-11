"""Turn the files `serve --capture DIR` wrote into a recording for `serve --replay`.

    uv run python demo/make_recording.py DIR/turn-7.json --title "Add discount codes" \
        --project shop-api --sample shop-api --check "python check_shop.py" --out demo/discount.json

Use `--checkout` instead of `--sample` when the project is this Cognitive Mesh checkout itself.
Read the result before you share it: it holds what the models answered.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--project", required=True, help="the project's name in the chat")
    parser.add_argument("--sample", default=None, help="a folder next to the recording")
    parser.add_argument("--checkout", action="store_true", help="the project is this checkout")
    parser.add_argument("--check", default=None, help="the check command as one string")
    parser.add_argument(
        "--revise-note",
        default=None,
        help="the capture is of a revision: the note, with the request ending '(revise: NOTE)'",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.sample) == args.checkout:
        raise SystemExit("give exactly one of --sample and --checkout")
    note = json.loads(args.capture.read_text(encoding="utf-8"))
    text = json.dumps(note["replies"])
    key = re.search(r"(?:ghp_|github_pat_|gsk_|AIza|sk-)[A-Za-z0-9_]{8,}", text)
    if key:
        raise SystemExit(f"the replies hold something that looks like a key: {key.group()[:6]}...")
    recording = {
        "title": args.title,
        "project": {
            "name": args.project,
            "sample": args.sample,
            "checkout": args.checkout,
            "check": shlex.split(args.check) if args.check else None,
        },
        "request": note["request"],
        "mode": note["mode"],
        "revise_note": args.revise_note,
        "conductor": note["conductor"],
        "replies": note["replies"],
    }
    args.out.write_text(json.dumps(recording, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"saved {len(note['replies'])} model replies to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
