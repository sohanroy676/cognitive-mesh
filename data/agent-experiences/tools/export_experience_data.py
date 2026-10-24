"""Write the experience database as readable JSON, using the same code the web UI uses.

    uv run --project E:/Projects/cognitive-mesh python export_experience_data.py DB GRAPH OUT_DIR

Writes OUT_DIR/runs.json, experiences.json and claims.json. The SQLite files stay the canonical copy;
these files exist so a reviewer can read and diff the records in git. Statuses of claims are computed
when they are viewed and are never stored: with no application folder given, each claim is `supported`
or `conflicting` and whether it is still current is unknown.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cognitive_mesh.ui.api import UiApi
from cognitive_mesh.ui.config import UiConfig


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    db, graph, out = Path(argv[0]), Path(argv[1]), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    api = UiApi.create(UiConfig(db=db, graph=graph, port=0))

    status, runs = api.traces({})
    assert status == 200, runs
    listing = runs["data"]
    full_runs = []
    for summary in sorted(listing["runs"], key=lambda r: (r["started_at"], r["run_id"])):
        s, body = api.trace({"id": summary["run_id"]})
        assert s == 200, body
        full_runs.append(body["data"])
    dump(out / "runs.json", {"malformed": listing["malformed"], "runs": full_runs})

    s, exp = api.experiences({})
    assert s == 200, exp
    summary_part = {k: v for k, v in exp["data"].items() if k != "rows"}
    details = []
    for row in exp["data"]["rows"]:
        s, one = api.experience({"id": row["id"]})
        assert s == 200, one
        details.append({"preview": row["preview"], **one["data"]})
    dump(out / "experiences.json", {"summary": summary_part, "experiences": details})

    s, claims = api.claims({})
    assert s == 200, claims
    dump(out / "claims.json", {"notes": claims["notes"], **claims["data"]})
    print(
        f"{len(full_runs)} runs, {len(details)} experiences, "
        f"{len(claims['data']['claims'])} claims written to {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
