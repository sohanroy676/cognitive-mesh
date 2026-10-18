"""What application identity (ADR-0021) does and does not separate.

Before v1.3.0 the relevance gate could not tell applications apart, and these tests pinned that.
Now applications given different ids (here `--app-id`) no longer share claims or raw traces. Two
applications that derive the SAME id (same manifest name and directory name, nothing declared)
still do, because the id is a local label and not proof of identity. Either way a foreign claim is
never `validated` for code that does not contain it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.retrieval.context import (
    REASON_OTHER_APPLICATION,
    REASON_OTHER_APPLICATION_RAW,
)
from test_e2e_sessions import App, Files, cli_session, make_app, task, write_plan


@pytest.fixture
def work(tmp_path: Path) -> Path:
    cwd = tmp_path / "cwd"  # an empty directory: no .env, no state but the files we name
    cwd.mkdir()
    return cwd


def twin(root: Path, like: App, imports: str) -> App:
    """Another application with the same directory name, package and module names."""
    app = App(root, like.token, like.pkg, like.alpha, like.beta, like.gamma)
    package = root / "src" / app.pkg
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{app.pkg}"\nversion = "0.1"\n', newline="\n"
    )
    (package / "__init__.py").write_text("", newline="\n")
    (package / f"{app.alpha}.py").write_text(f"from . import {imports}\n", newline="\n")
    (package / f"{app.beta}.py").write_text("VALUE = 1\n", newline="\n")
    (package / f"{app.gamma}.py").write_text("OTHER = 2\n", newline="\n")
    return app


def _two_sessions(
    tmp_path: Path, work: Path, first_args: tuple[str, ...], second_args: tuple[str, ...]
) -> tuple[App, str, dict[str, Any]]:
    """Session 1 on one application, then the same request against its same-named twin."""
    one = make_app(tmp_path / "one" / "shop_app")
    two = twin(tmp_path / "two" / "shop_app", one, one.gamma)  # alpha imports gamma here
    files = Files(tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3")
    steps: list[dict[str, object]] = [
        {"step_id": "report", "capability": "unresolved_report"},
        {
            "step_id": "deps",
            "capability": "dependencies_of",
            "inputs": {"node_id": one.node(one.alpha)},
        },
    ]
    plan = write_plan(tmp_path / "p.json", steps)
    code, first, _ = task(work, files, one, plan, f"dependencies of {one.alpha}", *first_args)
    assert code == 0
    assert cli_session(work, "memory", "index", "--db", files.db, "--index", files.index)[0] == 0
    assert (
        cli_session(work, "experience", "build", "--db", files.db, "--graph", files.graph)[0] == 0
    )
    code, data, err = task(
        work,
        files,
        two,
        plan,
        f"dependencies of {one.alpha}",
        "--min-similarity",
        "0",
        *second_args,
    )
    assert code == 0, err
    return one, str(first["trace_id"]), data


def test_the_same_derived_id_still_shares_context_but_not_validation(
    tmp_path: Path, work: Path
) -> None:
    one, first_id, data = _two_sessions(tmp_path, work, (), ())
    context = data["context"]
    claims = {c["name"]: c for c in context["claims"]}
    foreign = claims[f"{one.node(one.alpha)} depends_on {one.node(one.beta)}"]

    # LIMITATION (ADR-0021): both applications derive the same label, so it cannot tell them apart.
    assert {s["run_id"] for s in foreign["supporting"]} == {first_id}
    assert first_id in {r["run_id"] for r in context["raw"]}
    assert foreign["identity"] == "matched"
    # What still protects the agent: the claim is re-derived against this application's code.
    assert foreign["status"] == "stale"
    assert json.dumps(context)  # the pack stays a well-formed document


def test_different_declared_ids_keep_the_applications_apart(tmp_path: Path, work: Path) -> None:
    _, first_id, data = _two_sessions(
        tmp_path, work, ("--app-id", "shop-one"), ("--app-id", "shop-two")
    )
    context = data["context"]
    assert context["claims"] == [] and context["raw"] == [] and context["empty"] is True
    reasons = {n["reason"] for n in data["not_applicable"]}
    assert reasons == {REASON_OTHER_APPLICATION, REASON_OTHER_APPLICATION_RAW}
    assert first_id not in json.dumps(context)
