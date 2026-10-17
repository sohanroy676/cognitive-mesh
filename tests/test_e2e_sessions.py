"""The v1.0.0 workflow across separate processes (ADR-0017).

Every CLI call is its own Python process, so nothing survives except the files on disk. The
application is generated with random module names on every run, so the assertions cannot be met by
a hardcoded answer: only names read back from the recorded traces can satisfy them.
"""

import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.traces import TraceStore

DRIVER = Path(__file__).parent / "e2e_driver.py"
FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class App:
    root: Path
    token: str
    pkg: str
    alpha: str
    beta: str
    gamma: str

    def node(self, module: str) -> str:
        return f"module:src/{self.pkg}/{module}.py"


def make_app(root: Path) -> App:
    token = uuid.uuid4().hex[:8]
    app = App(root, token, f"pkg_{token}", f"alpha_{token}", f"beta_{token}", f"gamma_{token}")
    package = root / "src" / app.pkg
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{app.pkg}"\nversion = "0.1"\n', newline="\n"
    )
    (package / "__init__.py").write_text("", newline="\n")
    (package / f"{app.alpha}.py").write_text(f"from . import {app.beta}\n", newline="\n")
    (package / f"{app.beta}.py").write_text("VALUE = 1\n", newline="\n")
    (package / f"{app.gamma}.py").write_text("OTHER = 2\n", newline="\n")
    return app


def write_plan(path: Path, steps: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"task_id": path.stem, "steps": steps}), encoding="utf-8")
    return path


def cli_session(cwd: Path, *args: str | Path) -> tuple[int, str, str]:
    """One new process: the CLI with the fake embedder."""
    done = subprocess.run(  # noqa: S603  (our own interpreter and driver)
        [sys.executable, str(DRIVER), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return done.returncode, done.stdout, done.stderr


@dataclass
class Files:
    db: Path
    index: Path
    graph: Path


def task(
    cwd: Path, files: Files, app: App, plan: Path, request: str, *extra: str
) -> tuple[int, dict[str, Any], str]:
    code, out, err = cli_session(
        cwd,
        "task",
        request,
        "--plan",
        plan,
        "--root",
        app.root,
        "--db",
        files.db,
        "--index",
        files.index,
        "--graph",
        files.graph,
        "--json",
        *extra,
    )
    return code, json.loads(out), err


def by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = data["context"]
    assert isinstance(context, dict)
    return {c["name"]: c for c in context["claims"]}


@pytest.fixture
def work(tmp_path: Path) -> Path:
    cwd = tmp_path / "cwd"  # an empty directory: no .env, no state but the files we name
    cwd.mkdir()
    return cwd


def test_experience_survives_a_restart_and_reaches_a_related_task_labelled(
    tmp_path: Path, work: Path
) -> None:
    app = make_app(tmp_path / "shop_app")
    files = Files(tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3")
    alpha = app.node(app.alpha)
    plans = tmp_path / "plans"
    plans.mkdir()
    deps = write_plan(
        plans / "s1.json",
        [
            {"step_id": "report", "capability": "unresolved_report"},
            {
                "step_id": "deps",
                "capability": "dependencies_of",
                "inputs": {"node_id": alpha},
                "depends_on": ["report"],
            },
        ],
    )
    needs_model = write_plan(
        plans / "fails.json",
        [
            {"step_id": "report", "capability": "unresolved_report"},
            {
                "step_id": "explain",
                "capability": "explain_file",
                "inputs": {"path": "pyproject.toml"},
                "depends_on": ["report"],
            },
        ],
    )
    later = write_plan(
        plans / "s2.json",
        [
            {"step_id": "cmp", "capability": "compare_with_recalled"},
            {"step_id": "deps", "capability": "dependencies_of", "inputs": {"node_id": alpha}},
        ],
    )

    # -- session 1: a task on the original code, and one that fails (no model provider) ---------
    code, first, _ = task(work, files, app, deps, f"dependencies of {app.alpha}")
    assert code == 0
    assert first["context"]["empty"] is True  # nothing to recall yet
    first_id = str(first["trace_id"])
    code, failed, _ = task(work, files, app, needs_model, f"explain {app.alpha}")
    assert code == 7 and failed["run"]["status"] == "failed"
    failed_id = str(failed["trace_id"])

    # -- the code changes, and the next run sees the new version --------------------------------
    source = app.root / "src" / app.pkg / f"{app.alpha}.py"
    source.write_text(f"from . import {app.gamma}\n", newline="\n")
    code, edited, _ = task(work, files, app, deps, f"dependencies of {app.alpha}")
    assert code == 0
    edited_id = str(edited["trace_id"])

    # -- memory is built from the recorded traces -----------------------------------------------
    assert cli_session(work, "memory", "index", "--db", files.db, "--index", files.index)[0] == 0
    assert (
        cli_session(work, "experience", "build", "--db", files.db, "--graph", files.graph)[0] == 0
    )

    # -- session 2: a new process, a related task ------------------------------------------------
    request = f"what does {app.alpha} depend on now"
    code, second, err = task(work, files, app, later, request, "--min-similarity", "0", "--k", "20")
    assert code == 0, err
    claims = by_name(second)
    old_edge = claims[f"{alpha} depends_on {app.node(app.beta)}"]
    new_edge = claims[f"{alpha} depends_on {app.node(app.gamma)}"]

    # statuses are computed against the code as it is now, and carry their meaning
    assert old_edge["status"] == "stale" and new_edge["status"] == "validated"
    assert old_edge["meaning"] and new_edge["meaning"]
    # every claim can be traced to the runs that back it, and to the ones that differ
    assert {s["run_id"] for s in old_edge["supporting"]} == {first_id}
    assert {s["run_id"] for s in new_edge["supporting"]} == {edited_id}
    assert {c["run_id"] for c in old_edge["contradicting"]} == {edited_id}
    assert {c["run_id"] for c in new_edge["contradicting"]} == {first_id}
    assert all(c["same_version"] is False for c in new_edge["contradicting"])

    # the failed step is offered only as a raw trace, labelled; no dependency claim rests on it
    context = second["context"]
    failed_step = [
        r for r in context["raw"] if r["run_id"] == failed_id and r["step_id"] == "explain"
    ]
    assert [(r["kind"], r["label"]) for r in failed_step] == [
        ("failed", "raw trace, not knowledge")
    ]
    for edge in (old_edge, new_edge):
        runs = {s["run_id"] for s in edge["supporting"]} | {
            c["run_id"] for c in edge["contradicting"]
        }
        assert failed_id not in runs

    # the agent re-derived each claim from the current code instead of repeating the pack
    items = second["run"]["steps"][0]["attempts"][0]["output"]["items"]
    now = {i["claim_id"]: i["now"] for i in items}
    assert now[str(old_edge["claim_id"])] == "refuted"
    assert now[str(new_edge["claim_id"])] == "holds"

    # what the second run was offered is in its trace, read by this (a third) process
    second_id = str(second["trace_id"])
    with TraceStore.open_existing(files.db) as store:
        trace = store.get(second_id)
    assert trace.recalled is not None and trace.recalled.model_dump(mode="json") == context
    assert trace.request_length == len(request) and trace.status.value == "succeeded"
    code, shown, _ = cli_session(work, "traces", "show", second_id, "--db", files.db)
    assert code == 0 and "[stale]" in shown and "[validated]" in shown and "[raw: failed]" in shown


def test_an_unrelated_task_on_another_application_gets_nothing_misleading(
    tmp_path: Path, work: Path
) -> None:
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
    assert task(work, files, app, plan, f"dependencies of {app.alpha}")[0] == 0
    assert cli_session(work, "memory", "index", "--db", files.db, "--index", files.index)[0] == 0
    assert (
        cli_session(work, "experience", "build", "--db", files.db, "--graph", files.graph)[0] == 0
    )

    other = make_app(tmp_path / "billing_app")
    other_plan = write_plan(
        tmp_path / "q.json", [{"step_id": "report", "capability": "unresolved_report"}]
    )
    for extra in ([], ["--min-similarity", "0", "--k", "20"]):  # with and without the cut-off
        code, data, err = task(work, files, other, other_plan, "totally unrelated billing", *extra)
        assert code == 0, err
        context = data["context"]
        assert context["empty"] is True and context["claims"] == [] and context["raw"] == []
        assert app.token not in json.dumps(context)  # nothing of the other application
    # with the cut-off off, similarity lets the old claims through and the subject gate stops them
    assert context["withheld"]["not_applicable"] > 0
    assert data["not_applicable"] and all(n["reason"] for n in data["not_applicable"])
