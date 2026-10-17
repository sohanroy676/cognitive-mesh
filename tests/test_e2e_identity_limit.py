"""Known limitation of ADR-0017: the relevance gate does not know which application a run analysed.

The gate keeps a claim when its subject exists in the current dependency graph, and a raw trace
when the run's root directory has the same name. Two different applications that share a directory
name and module paths therefore share context. These tests pin that behaviour so that a future fix
(application identity, planned with the v1.3.0 environment identifiers) has to change them on
purpose. What must hold today: the foreign claim is never `validated` for code that does not
contain it, and it is labelled with the runs (of the other application) that back it.
"""

import json
from pathlib import Path

import pytest

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


def test_same_named_applications_share_context_but_not_validation(
    tmp_path: Path, work: Path
) -> None:
    one = make_app(tmp_path / "one" / "shop_app")
    two = twin(tmp_path / "two" / "shop_app", one, one.gamma)  # alpha imports gamma here
    files = Files(tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3")
    alpha = one.node(one.alpha)
    steps: list[dict[str, object]] = [
        {"step_id": "report", "capability": "unresolved_report"},
        {"step_id": "deps", "capability": "dependencies_of", "inputs": {"node_id": alpha}},
    ]
    plan = write_plan(tmp_path / "p.json", steps)
    code, first, _ = task(work, files, one, plan, f"dependencies of {one.alpha}")
    assert code == 0
    first_id = str(first["trace_id"])
    assert cli_session(work, "memory", "index", "--db", files.db, "--index", files.index)[0] == 0
    assert (
        cli_session(work, "experience", "build", "--db", files.db, "--graph", files.graph)[0] == 0
    )

    # the same request, now against the other application
    code, data, err = task(
        work, files, two, plan, f"dependencies of {one.alpha}", "--min-similarity", "0"
    )
    assert code == 0, err
    context = data["context"]
    claims = {c["name"]: c for c in context["claims"]}
    foreign = claims[f"{alpha} depends_on {one.node(one.beta)}"]

    # LIMITATION: a claim observed on the other application is offered here, backed by its run ...
    assert {s["run_id"] for s in foreign["supporting"]} == {first_id}
    # ... and a raw trace of the other application passes the same-directory-name check.
    assert first_id in {r["run_id"] for r in context["raw"]}
    # What still protects the agent: the claim is re-derived against this application's code.
    assert foreign["status"] != "validated"
    assert foreign["status"] == "stale"
    assert json.dumps(context)  # the pack stays a well-formed document
