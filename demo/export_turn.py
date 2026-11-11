"""Save one chat turn of a running home as a recording that `serve --replay` can show offline.

    uv run python demo/export_turn.py --home PATH_TO_HOME --turn 11 --out demo/discount-example.json

Reads the home's chat file read-only. Machine-specific text (the home folder, the user's folder)
is replaced; the recording holds no key or token, but read it before you share it.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

SKIP_KINDS = {"replay"}


def export(home: Path, turn_id: int) -> dict[str, object]:
    chat = home / "chat.sqlite3"
    if not chat.is_file():
        raise SystemExit(f"no chat file at {chat}")
    con = sqlite3.connect(f"file:{chat.as_posix()}?mode=ro", uri=True)
    row = con.execute(
        "SELECT project, text, mode, memory FROM turns WHERE id = ?", (turn_id,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"no turn {turn_id}")
    events = [
        {"kind": kind, "data": json.loads(data)}
        for kind, data in con.execute(
            "SELECT kind, data FROM turn_events WHERE turn_id = ? ORDER BY seq", (turn_id,)
        )
        if kind not in SKIP_KINDS
    ]
    text = json.dumps(events)
    for secret_like in re.findall(r"(?:ghp_|github_pat_|gsk_|AIza)[A-Za-z0-9_]+", text):
        raise SystemExit(f"the turn holds something that looks like a key: {secret_like[:6]}...")
    scrubbed = text.replace(str(home).replace("\\", "\\\\"), "<home>")
    return {
        "title": "Add a Discount class to the shop cart",
        "project": row[0],
        "request": row[1],
        "mode": row[2],
        "memory": bool(row[3]),
        "events": json.loads(scrubbed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--turn", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    recording = export(args.home, args.turn)
    args.out.write_text(json.dumps(recording, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"saved turn {args.turn} ({len(recording['events'])} events) to {args.out}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
