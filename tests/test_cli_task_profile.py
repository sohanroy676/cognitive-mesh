# ruff: noqa: F811  (pytest fixtures imported from test_cli_recall)
"""`task --profile` and declared plan state through the CLI (ADR-0023)."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.cli import EXIT_OK, EXIT_PLAN_ERROR, main
from cognitive_mesh.traces import TraceStore
from test_cli_recall import World, fake_embedder, world  # noqa: F401
from test_cli_task import COMPARE_PLAN, REQUEST

FIXTURES = Path(__file__).parent / "fixtures"


def task(w: World, plan: Path, *extra: str | Path) -> list[str]:
    return [
        str(a)
        for a in (
            "task",
            REQUEST,
            "--plan",
            plan,
            "--root",
            w.app,
            "--db",
            w.db,
            "--index",
            w.idx,
            "--graph",
            w.graph,
            *extra,
            "--json",
        )
    ]


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    path = tmp_path / "compare.json"
    path.write_text(json.dumps(COMPARE_PLAN), encoding="utf-8")
    return path


def context(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = json.loads(capsys.readouterr().out)
    pack: dict[str, object] = out["context"]
    return pack


def test_each_profile_has_its_own_defaults(
    world: World, plan: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(task(world, plan)) == EXIT_OK
    strict = context(capsys)
    assert (strict["profile"], strict["k"], strict["min_similarity"]) == ("precision", 5, 0.2)
    assert main(task(world, plan, "--profile", "recall")) == EXIT_OK
    wide = context(capsys)
    assert (wide["profile"], wide["k"], wide["min_similarity"]) == ("recall", 10, 0.1)


def test_explicit_values_win_over_the_profile_defaults(
    world: World, plan: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = task(world, plan, "--profile", "recall", "--k", "3", "--min-similarity", "0")
    assert main(args) == EXIT_OK
    got = context(capsys)
    assert (got["profile"], got["k"], got["min_similarity"]) == ("recall", 3, 0)


def test_an_unknown_profile_is_a_usage_error(world: World, plan: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(task(world, plan, "--profile", "everything"))
    assert exc.value.code == 2


def test_the_profile_is_recorded_with_the_run(
    world: World, plan: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(task(world, plan, "--profile", "recall")) == EXIT_OK
    run_id = json.loads(capsys.readouterr().out)["trace_id"]
    with TraceStore.open_existing(world.db) as store:
        recalled = store.get(run_id).recalled
    assert recalled is not None and recalled.profile == "recall"


def test_the_text_output_names_the_profile(
    world: World, plan: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [a for a in task(world, plan, "--profile", "recall") if a != "--json"]
    assert main(args) == EXIT_OK
    assert "profile: recall; conflicts kept:" in capsys.readouterr().out


def write_plan(tmp_path: Path, state: dict[str, object]) -> Path:
    path = tmp_path / "state_plan.json"
    body = {
        "task_id": "t",
        "state": state,
        "steps": [
            {
                "step_id": "d",
                "capability": "dependencies_of",
                "inputs": {"node_id": "module:src/dep_app/a.py"},
            },
        ],
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_a_plan_with_declared_state_loads_and_runs(tmp_path: Path) -> None:
    path = write_plan(tmp_path, {"deps": {"step_id": "d", "fields": "*"}})
    args = ["run", str(path), "--root", str(FIXTURES / "dep_app")]
    assert main(args) == EXIT_OK


def test_invalid_declared_state_is_a_plan_error(tmp_path: Path) -> None:
    path = write_plan(tmp_path, {"deps": {"step_id": "ghost"}})
    assert main(["run", str(path), "--root", str(FIXTURES / "dep_app")]) == EXIT_PLAN_ERROR


def test_the_compare_plan_runs_through_the_cli_with_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = FIXTURES / "plans" / "compare_deps.json"
    assert main(["run", str(plan), "--root", str(FIXTURES / "dep_app"), "--json"]) == EXIT_OK
    run = json.loads(capsys.readouterr().out)
    last = run["run"]["steps"][-1] if "run" in run else run["steps"][-1]
    assert last["status"] == "succeeded"
    assert last["attempts"][-1]["output"]["shared"] == ["module:src/dep_app/util.py"]
    assert {s["state"] for s in last["input_sources"]} == {"first", "second"}
