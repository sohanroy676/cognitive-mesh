"""Opt-in end-to-end check with the real embedding model. Skipped unless RUN_LIVE_EMBEDDINGS=1.

Same workflow as test_e2e_sessions, but every session runs `python -m cognitive_mesh` with the real
MiniLM and the default similarity cut-off, so it shows whether that cut-off keeps related experience
and drops unrelated experience on real embeddings. The first run downloads ~90 MB.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_e2e_sessions import Files, make_app, write_plan

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EMBEDDINGS") != "1", reason="set RUN_LIVE_EMBEDDINGS=1"
    ),
]


def session(cwd: Path, *args: str | Path) -> tuple[int, str, str]:
    done = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-m", "cognitive_mesh", *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    return done.returncode, done.stdout, done.stderr


def test_the_default_cutoff_keeps_related_experience_and_drops_unrelated(tmp_path: Path) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    app = make_app(tmp_path / "shop_app")
    files = Files(tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3")
    plan = write_plan(
        tmp_path / "p.json",
        [
            {"step_id": "report", "capability": "unresolved_report"},
            {
                "step_id": "deps",
                "capability": "dependencies_of",
                "inputs": {"node_id": app.node(app.alpha)},
            },
        ],
    )

    def task(request: str) -> dict[str, object]:
        code, out, err = session(
            work,
            *("task", request, "--plan", plan, "--root", app.root, "--db", files.db),
            *("--index", files.index, "--graph", files.graph, "--json"),
        )
        assert code == 0, err
        data: dict[str, object] = json.loads(out)
        return data

    task("dependencies of the alpha module")
    assert session(work, "memory", "index", "--db", files.db, "--index", files.index)[0] == 0
    assert session(work, "experience", "build", "--db", files.db, "--graph", files.graph)[0] == 0

    related = task("which modules does the alpha module import")
    context = related["context"]
    assert isinstance(context, dict)
    names = [c["name"] for c in context["claims"]]
    print("related:", names, related["not_applicable"])
    assert any(app.beta in n for n in names)

    unrelated = task("zebra quantum pastry")
    other = unrelated["context"]
    assert isinstance(other, dict)
    print("unrelated:", other["claims"], other["raw"])
    assert other["empty"] is True and other["claims"] == []
