"""The `decisions` commands, `memory index`/`recall` with decisions, end to end (ADR-0039)."""

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import chromadb
import pytest
from chromadb.config import Settings

from cognitive_mesh import cli
from cognitive_mesh.cli import main
from cognitive_mesh.experience.build import claim_id
from decisions_support import git
from memory_support import FakeEmbedder

FIXTURES = Path(__file__).parent / "fixtures"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
APP_ID = "dep-app@app"
A_B = claim_id("depends_on", A, B, "imports", APP_ID)
A_PY = "src/dep_app/a.py"
B_PY = "src/dep_app/b.py"


@dataclass
class World:
    app: Path
    db: Path
    idx: Path
    graph: Path


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())


def run(*argv: str | Path) -> int:
    return main([str(a) for a in argv])


@pytest.fixture
def world(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> World:
    app = tmp_path / "app"
    shutil.copytree(FIXTURES / "dep_app", app)
    w = World(app, tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3")
    plan = FIXTURES / "plans" / "ok.json"
    for _ in range(2):
        assert run("run", plan, "--root", app, "--trace-db", w.db) == 0
    capsys.readouterr()
    return w


def record(w: World, capsys: pytest.CaptureFixture[str], *extra: str | Path, **kw: str) -> str:
    """Record a decision and return its id."""
    args: list[str | Path] = [
        "decisions",
        "record",
        "--db",
        w.db,
        "--by",
        "alice",
        "--summary",
        kw.get("summary", "Keep b separate from a"),
        "--option",
        "keep apart",
        "--option",
        "merge them",
        "--chosen",
        "keep apart",
        "--reasoning",
        kw.get("reasoning", "a imports b and nothing else does"),
        "--json",
        *extra,
    ]
    assert run(*args) == 0
    return str(json.loads(capsys.readouterr().out)["decision"]["decision_id"])


def build(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("memory", "index", "--db", w.db, "--index", w.idx) == 0
    assert run("experience", "build", "--db", w.db, "--graph", w.graph) == 0
    capsys.readouterr()


# -- record -----------------------------------------------------------------------------------


def test_record_then_show_in_a_new_call_with_status_and_evidence(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys, "--cite-file", A_PY, "--root", world.app)
    assert run("decisions", "show", did, "--db", world.db, "--root", world.app) == 0
    out = capsys.readouterr().out
    assert f"[supported] {did}" in out
    assert "by alice (person)" in out
    assert f"[ok] file {A_PY}" in out
    assert "reasoning (person, data, not evidence)" in out
    assert "outcome for the current code version: none" in out
    assert "no --graph given" in out


def test_record_prints_what_it_observed_and_is_json_clean(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            "decisions",
            "record",
            "--db",
            world.db,
            "--by",
            "bob",
            "--summary",
            "s",
            "--option",
            "a",
            "--chosen",
            "a",
            "--reasoning",
            "r",
            "--root",
            world.app,
            "--cite-file",
            B_PY,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out.startswith("recorded decision:")
    assert f"application {APP_ID}" in out


@pytest.mark.parametrize(
    "extra",
    [
        ["--summary", "s"],  # missing options, chosen, reasoning
        ["--summary", "s", "--option", "a", "--chosen", "a"],  # missing reasoning
    ],
)
def test_record_without_the_content_is_a_usage_error(
    world: World, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    code = run("decisions", "record", "--db", world.db, "--by", "x", *extra)
    assert code == 2
    assert "--from-json" in capsys.readouterr().err


def test_record_refusals_have_their_own_exit_codes_and_store_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    base: list[str | Path] = [
        "decisions",
        "record",
        "--db",
        world.db,
        "--by",
        "x",
        "--summary",
        "s",
        "--option",
        "a",
        "--chosen",
        "a",
        "--reasoning",
        "r",
    ]
    assert run(*base, "--root", world.app / "missing") == 3
    assert run(*base, "--cite-file", A_PY) == 21  # a file needs --root
    assert run(*base, "--cite-file", "nope.py", "--root", world.app) == 21
    assert run(*base, "--cite-file", "../x.py", "--root", world.app) == 21
    assert run(*base, "--supersedes", "decision:" + "e" * 32) == 21
    assert run(*base, "--cite-node", "no-colon") == 21
    assert run(*base, "--run", "run-1") == 21  # run and step go together
    err = capsys.readouterr().err
    assert "does not exist" in err
    assert run("decisions", "list", "--db", world.db, "--json") == 0
    assert json.loads(capsys.readouterr().out)["decisions"] == []


def test_a_secret_in_the_reasoning_is_never_stored_or_printed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    did = record(world, capsys, reasoning=f"use {secret} for it")
    assert run("decisions", "show", did, "--db", world.db) == 0
    out = capsys.readouterr().out
    assert secret not in out
    assert "[REDACTED" in out
    assert (
        secret
        not in sqlite3.connect(world.db).execute("SELECT reasoning FROM decisions").fetchone()[0]
    )


def test_control_characters_in_stored_text_cannot_drive_the_terminal(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        "decisions",
        "record",
        "--db",
        world.db,
        "--by",
        "x",
        "--summary",
        "s\x1b[2J",
        "--option",
        "a",
        "--chosen",
        "a",
        "--reasoning",
        "r",
    )
    assert code == 21


def test_record_from_json(world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    good = tmp_path / "d.json"
    good.write_text(
        json.dumps(
            {
                "summary": "s",
                "options": ["a", "b"],
                "chosen": "a",
                "reasoning": "r\nline two",
                "cite_files": [A_PY],
            }
        ),
        encoding="utf-8",
    )
    code = run(
        "decisions",
        "record",
        "--db",
        world.db,
        "--by",
        "x",
        "--from-json",
        good,
        "--root",
        world.app,
    )
    assert code == 0
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"summary": "s", "extra": 1}), encoding="utf-8")
    assert run("decisions", "record", "--db", world.db, "--by", "x", "--from-json", bad) == 2
    bad.write_text("not json", encoding="utf-8")
    assert run("decisions", "record", "--db", world.db, "--by", "x", "--from-json", bad) == 2
    assert (
        run(
            "decisions",
            "record",
            "--db",
            world.db,
            "--by",
            "x",
            "--from-json",
            tmp_path / "missing.json",
        )
        == 2
    )
    assert (
        run(
            "decisions",
            "record",
            "--db",
            world.db,
            "--by",
            "x",
            "--from-json",
            good,
            "--summary",
            "also",
        )
        == 2
    )
    assert "cannot be combined" in capsys.readouterr().err


# -- status after the code changes --------------------------------------------------------------


def test_editing_a_cited_file_makes_the_decision_stale(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys, "--cite-file", A_PY, "--root", world.app)
    target = world.app / A_PY
    target.write_text(target.read_text() + "\nEXTRA = 1\n", newline="\n")
    assert run("decisions", "list", "--db", world.db, "--root", world.app) == 0
    out = capsys.readouterr().out
    assert f"[stale] {did}" in out
    assert run("decisions", "show", did, "--db", world.db, "--root", world.app) == 0
    shown = capsys.readouterr().out
    assert f"[changed] file {A_PY}" in shown
    assert "other content than when the decision was recorded" in shown
    target.unlink()
    assert run("decisions", "list", "--db", world.db, "--root", world.app) == 0
    assert f"[unsupported] {did}" in capsys.readouterr().out


def test_a_cited_claim_is_checked_against_the_graph(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys, "--cite-node", A_B, "--cite-node", "claim:" + "9" * 32)
    build(world, capsys)
    assert run("decisions", "show", did, "--db", world.db, "--graph", world.graph) == 0
    out = capsys.readouterr().out
    assert "[unsupported]" in out  # the second node is not in the graph
    assert f"[absent] node claim:{'9' * 32}" in out
    assert f"[ok] node {A_B}" in out


def test_an_outcome_recorded_for_the_decision_validates_it_for_that_code_version(
    world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    did = record(world, capsys, "--cite-file", A_PY, "--root", world.app)
    build(world, capsys)
    outcomes = tmp_path / "o.db"
    code = run(
        "experience",
        "outcome",
        did,
        "--source",
        "check",
        "--result",
        "succeeded",
        "--detail",
        "tests passed",
        "--by",
        "ci",
        "--root",
        world.app,
        "--graph",
        world.graph,
        "--outcomes",
        outcomes,
    )
    assert code == 0
    assert "how the decision worked out" in capsys.readouterr().out
    shown: list[str | Path] = [
        "decisions",
        "show",
        did,
        "--db",
        world.db,
        "--root",
        world.app,
        "--outcomes",
        outcomes,
    ]
    assert run(*shown) == 0
    out = capsys.readouterr().out
    assert f"[validated] {did}" in out
    assert "outcome for the current code version: succeeded" in out
    # the code moves on: the outcome no longer applies
    (world.app / A_PY).write_text("X = 1\n", newline="\n")
    assert run(*shown) == 0
    assert "[validated]" not in capsys.readouterr().out
    # a promotion is for claims only
    assert (
        run(
            "experience",
            "promote",
            did,
            "--by",
            "x",
            "--db",
            world.db,
            "--graph",
            world.graph,
            "--root",
            world.app,
            "--outcomes",
            outcomes,
        )
        != 0
    )
    capsys.readouterr()


def test_an_outcomes_file_that_is_missing_or_not_one_gives_its_own_exit_code(
    world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    did = record(world, capsys)
    assert run("decisions", "show", did, "--db", world.db, "--outcomes", tmp_path / "no.db") == 16
    assert run("decisions", "show", did, "--db", world.db, "--reviews", tmp_path / "no.db") == 15
    assert run("decisions", "show", did, "--db", world.db, "--graph", tmp_path / "no.db") == 12
    assert run("decisions", "show", did, "--db", world.db, "--root", tmp_path / "no") == 3
    capsys.readouterr()


# -- links and trace ---------------------------------------------------------------------------


def test_trace_from_a_pull_request_back_to_the_decisions_and_their_evidence(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    first = record(world, capsys, "--cite-file", A_PY, "--root", world.app, summary="first")
    second = record(
        world,
        capsys,
        "--cite-file",
        A_PY,
        "--root",
        world.app,
        "--supersedes",
        first,
        summary="second",
    )
    assert (
        run("decisions", "link", second, "--db", world.db, "--pr", "org/repo#12", "--by", "al") == 0
    )
    assert "declared, not checked" in capsys.readouterr().out
    assert (
        run("decisions", "trace", "--db", world.db, "--root", world.app, "--pr", "org/repo#12") == 0
    )
    out = capsys.readouterr().out
    assert f"[supported] {second}" in out and f"[superseded] {first}" in out
    assert "declared pr org/repo#12" in out
    assert out.index("asked for") < out.index("related (supersession)")
    assert run("decisions", "trace", "--db", world.db, "--pr", "org/repo#99") == 13
    assert run("decisions", "trace", "--db", world.db) == 2
    assert run("decisions", "trace", second, "--db", world.db, "--pr", "x/y#1") == 2
    assert run("decisions", "trace", "decision:" + "d" * 32, "--db", world.db) == 13
    capsys.readouterr()
    assert run("decisions", "trace", second, "--db", world.db, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["roots"] == [second]
    assert {d["decision"]["decision_id"] for d in payload["decisions"]} == {first, second}


def test_link_refusals(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    did = record(world, capsys)
    assert run("decisions", "link", did, "--db", world.db, "--commit", "xyz", "--by", "a") == 21
    assert (
        run("decisions", "link", did, "--db", world.db, "--branch", "has space", "--by", "a") == 21
    )
    assert (
        run(
            "decisions",
            "link",
            "decision:" + "c" * 32,
            "--db",
            world.db,
            "--pr",
            "a/b#1",
            "--by",
            "a",
        )
        == 21
    )
    assert (
        run("decisions", "link", "nonsense", "--db", world.db, "--pr", "a/b#1", "--by", "a") == 21
    )
    capsys.readouterr()


def test_the_commit_and_branch_of_a_git_root_are_observed_and_traceable(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    git(world.app, "init", "-q", "-b", "feature/x")
    git(world.app, "add", ".")
    git(world.app, "commit", "-q", "-m", "init")
    head = git(world.app, "rev-parse", "HEAD")
    did = record(world, capsys, "--root", world.app, "--cite-file", A_PY)
    assert run("decisions", "trace", "--db", world.db, "--commit", head[:10]) == 0
    out = capsys.readouterr().out
    assert did in out and f"observed commit {head}" in out and "observed branch feature/x" in out
    assert run("decisions", "trace", "--db", world.db, "--branch", "feature/x") == 0
    assert did in capsys.readouterr().out
    assert (
        run("decisions", "trace", "--db", world.db, "--commit", "abc") == 13
    )  # too short to match


def test_a_root_that_is_not_a_git_repository_records_no_observed_link(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys, "--root", world.app)
    assert run("decisions", "show", did, "--db", world.db, "--json") == 0
    assert json.loads(capsys.readouterr().out)["links"] == []


# -- reading --------------------------------------------------------------------------------


def test_reading_commands_never_create_or_change_the_database(
    world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    did = record(world, capsys)
    before = world.db.read_bytes()
    for cmd in (["list"], ["show", did], ["trace", did]):
        assert run("decisions", *cmd, "--db", world.db) == 0
    capsys.readouterr()
    assert world.db.read_bytes() == before
    missing = tmp_path / "missing.sqlite3"
    assert run("decisions", "list", "--db", missing) == 8
    assert not missing.exists()
    assert run("decisions", "show", "decision:" + "b" * 32, "--db", world.db) == 13


def test_a_schema_five_database_must_be_upgraded_by_writing_first(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(world.db)
    for table in ("change_events", "changes", "decision_links", "decision_citations", "decisions"):
        conn.execute(f"DROP TABLE {table}")  # noqa: S608
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    assert run("decisions", "list", "--db", world.db) == 8
    assert "older version" in capsys.readouterr().err
    record(world, capsys)  # writing upgrades it
    assert run("decisions", "list", "--db", world.db) == 0


def test_a_row_edited_behind_the_programs_back_is_reported_and_not_shown(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys)
    conn = sqlite3.connect(world.db)
    conn.execute("DROP TRIGGER decisions_no_update")
    conn.execute("UPDATE decisions SET chosen = 'sneaky'")
    conn.commit()
    conn.close()
    assert run("decisions", "list", "--db", world.db) == 0
    captured = capsys.readouterr()
    assert did not in captured.out
    assert "unreadable decision row" in captured.err and did in captured.err
    assert run("decisions", "show", did, "--db", world.db) == 21


# -- index and recall -------------------------------------------------------------------------


def test_memory_index_indexes_decisions_and_repeating_it_changes_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    record(world, capsys)
    assert run("memory", "index", "--db", world.db, "--index", world.idx) == 0
    assert "decisions: 1 added, 0 updated, 0 unchanged, 0 orphaned" in capsys.readouterr().out
    assert run("memory", "index", "--db", world.db, "--index", world.idx) == 0
    assert "decisions: 0 added, 0 updated, 1 unchanged, 0 orphaned" in capsys.readouterr().out
    assert run("memory", "index", "--db", world.db, "--index", world.idx, "--json") == 0
    assert json.loads(capsys.readouterr().out)["decisions"]["unchanged"] == 1


def test_recall_finds_a_decision_by_meaning_and_through_a_cited_claim(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    by_meaning = record(world, capsys, summary="banana pudding recipe", reasoning="sugar and eggs")
    cites = record(world, capsys, "--cite-node", A_B, summary="unrelated words xyz")
    build(world, capsys)
    base: list[str | Path] = [
        "recall",
        "banana pudding recipe",
        "--db",
        world.db,
        "--index",
        world.idx,
        "--graph",
        world.graph,
        "--root",
        world.app,
        "--min-similarity",
        "0.3",
    ]
    assert run(*base) == 0
    out = capsys.readouterr().out
    assert by_meaning in out
    assert "retrieved by meaning" in out
    graph_query: list[str | Path] = [
        "recall",
        "dependencies_of module:src/dep_app/a.py",
        "--db",
        world.db,
        "--index",
        world.idx,
        "--graph",
        world.graph,
        "--min-similarity",
        "0",
    ]
    assert run(*graph_query) == 0
    out = capsys.readouterr().out
    assert f"{cites}" in out
    assert f"cites recalled {A_B}" in out
    assert run(*graph_query, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    found = {d["assessment"]["decision_id"]: d for d in payload["decisions"]["decisions"]}
    assert A_B in found[cites]["cites_recalled"]


def test_recall_with_an_index_built_before_decisions_still_works_and_says_so(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    record(world, capsys)
    build(world, capsys)
    client = chromadb.PersistentClient(
        path=str(world.idx), settings=Settings(anonymized_telemetry=False)
    )
    client.delete_collection("decisions")
    args: list[str | Path] = [
        "recall",
        "dependencies of a",
        "--db",
        world.db,
        "--index",
        world.idx,
        "--graph",
        world.graph,
        "--min-similarity",
        "0",
    ]
    assert run(*args) == 0
    assert "no decisions collection" in capsys.readouterr().out
    assert run(*args, "--json") == 0
    assert json.loads(capsys.readouterr().out)["decisions"]["indexed"] is False
    assert run("memory", "search", "dependencies", "--db", world.db, "--index", world.idx) == 0


def test_a_decision_row_that_changes_after_indexing_is_not_confirmed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    did = record(world, capsys, summary="banana pudding recipe")
    build(world, capsys)
    conn = sqlite3.connect(world.db)
    conn.execute("DROP TRIGGER decisions_no_delete")
    conn.execute("DELETE FROM decision_citations")
    conn.execute("DELETE FROM decisions")
    conn.commit()
    conn.close()
    args: list[str | Path] = [
        "recall",
        "banana pudding recipe",
        "--db",
        world.db,
        "--index",
        world.idx,
        "--graph",
        world.graph,
        "--min-similarity",
        "0.3",
    ]
    assert run(*args) == 0
    out = capsys.readouterr().out
    assert f"{did}  NOT CONFIRMED (source_missing)" in out
    assert f"[supported] {did}" not in out


def test_recall_labels_a_model_written_decision(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    from cognitive_mesh.decisions.models import MadeBy
    from cognitive_mesh.decisions.record import record_decision
    from cognitive_mesh.traces import TraceStore

    with TraceStore.create(world.db) as store:
        record_decision(
            store,
            summary="banana pudding recipe",
            options=["a"],
            chosen="a",
            reasoning="r",
            made_by=MadeBy.MODEL,
            provider="stub",
            model="m",
            role="planner",
            app_id=APP_ID,
        )
    build(world, capsys)
    args: list[str | Path] = [
        "recall",
        "banana pudding recipe",
        "--db",
        world.db,
        "--index",
        world.idx,
        "--graph",
        world.graph,
        "--min-similarity",
        "0.3",
    ]
    assert run(*args) == 0
    out = capsys.readouterr().out
    section = out[out.index("decisions (") :]
    assert "[supported]" not in section and "[validated]" not in section  # it cites nothing
    assert "[unsupported]" in section
    assert "written by a model; the reasoning is not verified" in section


def test_recall_of_decisions_through_the_graph_alone_when_the_index_has_none(
    tmp_path: Path,
) -> None:
    from cognitive_mesh.experience import ExperienceGraph, build_from_traces
    from cognitive_mesh.experience.decision_view import assess_all
    from cognitive_mesh.memory.models import DecisionSearchResult
    from cognitive_mesh.retrieval import recall_decisions
    from cognitive_mesh.traces import TraceStore
    from decisions_support import person
    from experience_support import deps_step
    from experience_support import make_db as make_experience_db

    class NoDecisions:
        has_decisions = False

        def search_decisions(
            self, query: str, store: TraceStore, *, k: int, min_similarity: float
        ) -> DecisionSearchResult:
            raise AssertionError("must not search an index that has no decisions collection")

    db = make_experience_db(
        tmp_path, {"run-1": ([deps_step("run-1", "s1", "module:a.py", ["module:b.py"])], None)}
    )
    with TraceStore.open_existing(db) as store:
        claim = next(n.id for n in ExperienceGraph(build_from_traces(store)).claims())
    with TraceStore.create(db) as store:
        d = person(store, cite_nodes=[claim])
        graph = ExperienceGraph(build_from_traces(store))
        view = assess_all(store, graph=graph)
        result = recall_decisions(
            "q", NoDecisions(), store, graph, [claim], view.assessments, k=5, min_similarity=0.2
        )
        nothing = recall_decisions(
            "q", NoDecisions(), store, graph, ["claim:" + "0" * 32], view.assessments,
            k=5, min_similarity=0.2,
        )  # fmt: skip
    assert result.indexed is False
    assert [x.assessment.decision_id for x in result.decisions] == [d.decision_id]
    assert result.decisions[0].similarity is None
    assert result.decisions[0].cites_recalled == (claim,)
    assert "no decisions collection" in result.message
    assert nothing.decisions == ()
