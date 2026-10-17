# ruff: noqa: F811  (pytest fixtures imported from test_cli_recall)
"""`task`: a plan runs with recalled experience as context, and what was offered is recorded."""

import json
import shutil
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_EXPERIENCE_ERROR,
    EXIT_INGESTION_ERROR,
    EXIT_MEMORY_ERROR,
    EXIT_OK,
    EXIT_PLAN_ERROR,
    EXIT_REVIEW_STORE_ERROR,
    EXIT_TASK_NOT_SUCCEEDED,
    main,
)
from cognitive_mesh.traces import TraceStore
from memory_support import FakeEmbedder
from test_cli_recall import World, fake_embedder, world  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
PLANS = FIXTURES / "plans"
REQUEST = "dependencies_of module:src/dep_app/a.py"
COMPARE_PLAN = {
    "task_id": "later",
    "steps": [{"step_id": "cmp", "capability": "compare_with_recalled"}],
}


def run(*argv: str | Path) -> int:
    return main([str(a) for a in argv])


def task_args(
    w: World, plan: Path, root: Path | None = None, request: str = REQUEST, *extra: str | Path
) -> list[str | Path]:
    return [
        "task",
        request,
        "--plan",
        plan,
        "--root",
        root or w.app,
        "--db",
        w.db,
        "--index",
        w.idx,
        "--graph",
        w.graph,
        "--min-similarity",
        "0",
        *extra,
    ]


@pytest.fixture
def compare_plan(tmp_path: Path) -> Path:
    path = tmp_path / "compare.json"
    path.write_text(json.dumps(COMPARE_PLAN), encoding="utf-8")
    return path


def task_json(
    w: World, plan: Path, capsys: pytest.CaptureFixture[str], root: Path | None = None
) -> dict[str, object]:
    assert run(*task_args(w, plan, root, REQUEST, "--json")) == EXIT_OK
    data: dict[str, object] = json.loads(capsys.readouterr().out)
    return data


def recorded(db: Path, run_id: object) -> object:
    with TraceStore.open_existing(db) as store:
        return store.get(str(run_id))


def test_a_first_session_runs_with_an_explicit_empty_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    args: list[str | Path] = [
        "task",
        REQUEST,
        "--plan",
        PLANS / "ok.json",
        "--root",
        FIXTURES / "dep_app",
    ]
    args += ["--db", db, "--index", tmp_path / "idx", "--graph", tmp_path / "g.sqlite3"]
    assert run(*args) == EXIT_OK
    out = capsys.readouterr().out
    assert "no prior context" in out and "the memory index does not exist" in out
    assert "task demo: succeeded" in out and "trace: " in out
    assert not (tmp_path / "idx").exists() and not (tmp_path / "g.sqlite3").exists()
    with TraceStore.open_existing(db) as store:
        (summary,) = store.list().runs
        trace = store.get(summary.run_id)
    assert trace.recalled is not None and trace.recalled.empty
    assert trace.request_length == len(REQUEST)


def test_a_later_session_gets_labelled_claims_and_the_agent_rechecks_them(
    world: World,
    compare_plan: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = task_json(world, compare_plan, capsys)
    context = data["context"]
    assert isinstance(context, dict) and context["empty"] is False
    by_name = {c["name"]: c for c in context["claims"]}
    statuses = {c["status"] for c in by_name.values()}
    assert statuses == {"stale", "validated"}  # the removed import and the one that replaced it
    assert all(c["meaning"] and c["supporting"] for c in by_name.values())  # ids, not just words
    assert all(c["supporting"][0]["run_id"] for c in by_name.values())
    run_payload = data["run"]
    assert isinstance(run_payload, dict)
    output = run_payload["steps"][0]["attempts"][0]["output"]
    now = {i["claim_id"]: i["now"] for i in output["items"]}
    expected = {
        c["claim_id"]: ("refuted" if c["status"] == "stale" else "holds") for c in by_name.values()
    }
    assert now == expected  # computed from the current code, not copied from the pack


def test_what_was_offered_is_recorded_with_the_run_and_read_in_a_new_connection(
    world: World,
    compare_plan: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = task_json(world, compare_plan, capsys)
    trace = recorded(world.db, data["trace_id"])
    assert trace.recalled is not None and trace.recalled.model_dump(mode="json") == data["context"]  # type: ignore[attr-defined]
    assert trace.request_length == len(REQUEST)  # type: ignore[attr-defined]
    assert run("traces", "show", str(data["trace_id"]), "--db", world.db) == EXIT_OK
    shown = capsys.readouterr().out
    count = len(data["context"]["claims"])  # type: ignore[index]
    assert f"offered context: {count} claim(s)" in shown and count > 2
    assert "[stale]" in shown and "[validated]" in shown


def test_a_failed_task_still_records_what_it_was_offered_and_exits_7(
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = task_args(world, PLANS / "needs_model.json", FIXTURES / "sample_app", REQUEST, "--json")
    assert run(*args) == EXIT_TASK_NOT_SUCCEEDED
    data = json.loads(capsys.readouterr().out)
    assert data["run"]["status"] == "failed"
    trace = recorded(world.db, data["trace_id"])
    assert trace.recalled is not None  # type: ignore[attr-defined]
    assert trace.status.value == "failed"  # type: ignore[attr-defined]


def test_an_unrelated_task_on_another_application_gets_no_prior_context(
    world: World,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = tmp_path / "other_app"
    (other / "src" / "shop").mkdir(parents=True)
    (other / "pyproject.toml").write_text('[project]\nname = "shop"\nversion = "1"\n', newline="\n")
    (other / "src" / "shop" / "__init__.py").write_text("", newline="\n")
    (other / "src" / "shop" / "cart.py").write_text("import json\n", newline="\n")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"task_id": "x", "steps": [{"step_id": "r", "capability": "unresolved_report"}]})
    )
    assert (
        run(*task_args(world, plan, other, "unrelated question about carts", "--json")) == EXIT_OK
    )
    data = json.loads(capsys.readouterr().out)
    assert data["context"]["empty"] is True and data["context"]["claims"] == []
    assert data["context"]["withheld"]["not_applicable"] > 0
    assert data["not_applicable"] and all(n["reason"] for n in data["not_applicable"])
    assert "module:src/dep_app" not in json.dumps(data["context"])  # nothing of it reaches agents


@pytest.mark.parametrize(
    "request_text", ["", "   ", "x" * 2001, "line\nbreak", "tab\there", "nul\x00"]
)
def test_a_bad_request_is_refused_before_anything_is_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], request_text: str
) -> None:
    db = tmp_path / "t.sqlite3"
    args: list[str | Path] = [
        "task",
        request_text,
        "--plan",
        PLANS / "ok.json",
        "--root",
        FIXTURES / "dep_app",
    ]
    args += ["--db", db, "--index", tmp_path / "i", "--graph", tmp_path / "g"]
    assert run(*args) == EXIT_CONFIG_ERROR
    assert not db.exists()
    assert request_text.strip() == "" or request_text not in capsys.readouterr().err


def test_plan_and_root_errors_keep_their_exit_codes_and_create_no_database(
    tmp_path: Path,
) -> None:
    base: list[str | Path] = [
        "--db",
        tmp_path / "t.sqlite3",
        "--index",
        tmp_path / "i",
        "--graph",
        tmp_path / "g",
    ]
    ok, root = PLANS / "ok.json", FIXTURES / "dep_app"
    assert (
        run("task", "q", "--plan", PLANS / "cycle.json", "--root", root, *base) == EXIT_PLAN_ERROR
    )
    assert run("task", "q", "--plan", PLANS / "nope.json", "--root", root, *base) == EXIT_PLAN_ERROR
    assert (
        run("task", "q", "--plan", ok, "--root", tmp_path / "gone", *base) == EXIT_INGESTION_ERROR
    )
    assert not (tmp_path / "t.sqlite3").exists()


def test_hits_are_capped(tmp_path: Path) -> None:
    args: list[str | Path] = [
        "task",
        "q",
        "--plan",
        PLANS / "ok.json",
        "--root",
        FIXTURES / "dep_app",
        "--k",
        "21",
    ]
    args += ["--db", tmp_path / "t", "--index", tmp_path / "i", "--graph", tmp_path / "g"]
    with pytest.raises(SystemExit) as exit_info:
        run(*args)
    assert exit_info.value.code == 2


def test_a_foreign_index_a_damaged_graph_and_a_missing_reviews_file_are_errors_not_empty(
    world: World,
    compare_plan: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder("another-model"))
    assert run(*task_args(world, compare_plan)) == EXIT_MEMORY_ERROR
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())
    assert run(*task_args(world, compare_plan, None, REQUEST, "--reviews", tmp_path / "no.db")) == (
        EXIT_REVIEW_STORE_ERROR
    )
    damaged = tmp_path / "damaged.sqlite3"
    shutil.copy(world.graph, damaged)
    damaged.write_bytes(damaged.read_bytes()[:200])
    args = task_args(world, compare_plan)
    args[args.index("--graph") + 1] = damaged
    assert run(*args) == EXIT_EXPERIENCE_ERROR
    with TraceStore.open_existing(world.db) as store:  # none of the failed attempts left a run
        assert all(r.task_id != "later" for r in store.list().runs)
