"""The Experiences tab: every recorded step, read from the trace database (ADR-0033)."""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.agents import Basis
from cognitive_mesh.memory.documents import eligible_documents
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.ui import experiences as exp_module
from cognitive_mesh.ui.config import UiConfig
from memory_support import add_run, failure, success
from ui_support import A_UTIL, World, digest, serving

STATIC = Path(__file__).parent.parent / "src" / "cognitive_mesh" / "ui" / "static"


def get(c: Any) -> dict[str, Any]:
    reply = c.get("/api/experiences")
    assert reply.status == 200, reply.body
    data: dict[str, Any] = reply.json()["data"]
    return data


def mixed_db(tmp_path: Path) -> Path:
    """One run with an analyzer step, a model step and a failed step; and an unfinished run."""
    db = tmp_path / "mixed.sqlite3"
    add_run(
        db,
        "run-a",
        [
            (
                "s1",
                "dependencies_of",
                {},
                success("run-a", "s1", "dependencies_of", {"secret": "SENTINEL-OUTPUT"}),
            ),
            (
                "s2",
                "explain_file",
                {},
                success("run-a", "s2", "explain_file", {"text": "x"}, basis=Basis.MODEL_UNVERIFIED),
            ),
            ("s3", "dependencies_of", {}, failure("run-a", "s3", "dependencies_of", "boom")),
        ],
    )
    add_run(
        db,
        "run-unfinished",
        [("s1", "dependencies_of", {}, success("run-unfinished", "s1", "dependencies_of", {}))],
        finish=False,
    )
    return db


def test_every_recorded_step_is_listed_newest_run_first(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        data = get(c)
    assert data["total"] == data["shown"] == len(data["rows"]) == 6
    assert data["runs"] == 3 and data["truncated"] is False
    stamps = [r["started_at"] for r in data["rows"]]
    assert stamps == sorted(stamps, reverse=True)
    assert {r["capability"] for r in data["rows"]} == {"dependencies_of", "unresolved_report"}
    assert all(r["result"] == "success" and r["basis"] == "deterministic" for r in data["rows"])
    assert data["counts"]["capability"] == {"dependencies_of": 3, "unresolved_report": 3}


def test_the_list_is_exactly_the_eligible_steps_of_the_database(ui_world: World) -> None:
    with TraceStore.open_existing(ui_world.db) as store:
        expected = sorted(
            d.doc_id for _, t in store.iter_runs() if t for d in eligible_documents(t)
        )
    with serving(ui_world.config()) as c:
        got = sorted(r["id"] for r in get(c)["rows"])
    assert got == expected and len(got) == 6


def test_claims_come_from_the_graph_and_are_absent_without_it(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        with_graph = get(c)
    newest_deps = next(r for r in with_graph["rows"] if r["capability"] == "dependencies_of")
    assert any(cl["id"] == A_UTIL for cl in newest_deps["claims"])
    assert with_graph["graph"]["given"] and with_graph["graph"]["built_at"]
    assert with_graph["graph"]["not_in_graph"] == 0
    assert with_graph["graph"]["missing_from_database"] == 0
    with serving(ui_world.config(graph=None)) as c:
        without = get(c)
    assert without["graph"] == {
        "given": False,
        "built_at": None,
        "not_in_graph": None,
        "missing_from_database": None,
    }
    assert all(r["claims"] is None for r in without["rows"]) and without["total"] == 6


def test_a_graph_older_than_the_database_is_reported_with_counts(
    ui_world: World, tmp_path: Path
) -> None:
    db = tmp_path / "t.sqlite3"
    shutil.copy(ui_world.db, db)
    new_step = success("run-new", "s", "dependencies_of", {})
    new_steps: list[Any] = [("s", "dependencies_of", {}, new_step)]
    add_run(db, "run-new", new_steps)
    with serving(ui_world.config(db=db)) as c:
        reply = c.get("/api/experiences")
        data = reply.json()["data"]
        assert data["total"] == 7 and data["graph"]["not_in_graph"] == 1
        assert any("not in the graph yet" in w for w in reply.json()["warnings"])
        row = next(r for r in data["rows"] if r["run_id"] == "run-new")
        assert row["claims"] == []  # shown, with no claims, not hidden


def test_graph_steps_that_left_the_database_are_counted(ui_world: World, tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    shutil.copy(ui_world.db, db)
    with serving(ui_world.config(db=db)) as c:
        victim = get(c)["rows"][0]["run_id"]
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs WHERE run_id = ?", (victim,))
    conn.commit()
    conn.close()
    with serving(ui_world.config(db=db)) as c:
        reply = c.get("/api/experiences")
        data = reply.json()["data"]
        assert data["graph"]["missing_from_database"] >= 1 and data["total"] == 4
        assert any("no longer in the database" in w for w in reply.json()["warnings"])


def test_failed_and_model_written_steps_are_listed_and_unfinished_runs_are_not(
    tmp_path: Path,
) -> None:
    with serving(UiConfig(db=mixed_db(tmp_path), port=0)) as c:
        data = get(c)
    by_step = {r["step_id"]: r for r in data["rows"]}
    assert set(by_step) == {"s1", "s2", "s3"} and data["total"] == 3
    assert by_step["s1"]["basis"] == "deterministic" and by_step["s1"]["result"] == "success"
    assert by_step["s2"]["basis"] == "model_unverified"
    assert by_step["s3"]["result"] == "failure" and by_step["s3"]["basis"] is None
    assert data["counts"]["result"] == {"failure": 1, "success": 2}
    assert data["counts"]["basis"] == {"deterministic": 1, "model_unverified": 1, "none": 1}
    assert data["runs"] == 1 and data["runs_without_experiences"] == 1
    assert not any(r["run_id"] == "run-unfinished" for r in data["rows"])


def test_a_row_carries_a_short_preview_and_never_the_whole_output(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    big: Any = {"pad": "x" * 400, "tail": "SENTINEL-TAIL"}
    add_run(
        db, "run-p", [("s", "dependencies_of", {}, success("run-p", "s", "dependencies_of", big))]
    )
    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get("/api/experiences")
    assert b"SENTINEL-TAIL" not in reply.body  # past the 140 characters
    row = reply.json()["data"]["rows"][0]
    assert len(row["preview"]) == 140 and row["preview"].endswith("...")
    assert not {"text", "output", "result_json"} & set(row)


def test_a_long_list_is_cut_visibly_and_keeps_the_newest(
    ui_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exp_module, "MAX_ROWS", 2)
    with serving(ui_world.config()) as c:
        data = get(c)
    assert data["total"] == 6 and data["shown"] == 2 and data["truncated"] is True
    assert data["limit"] == 2
    assert sum(data["counts"]["capability"].values()) == 6  # counts cover everything


def test_markup_in_a_step_or_capability_comes_back_as_inert_text(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    step = "<b>x</b>&'s"
    add_run(
        db, "run-h", [(step, "dependencies_of", {}, success("run-h", step, "dependencies_of", {}))]
    )
    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get("/api/experiences")
    assert reply.headers["content-type"] == "application/json"
    assert reply.json()["data"]["rows"][0]["step_id"] == step


def test_listing_writes_nothing(ui_world: World) -> None:
    before = digest(ui_world.files())
    with serving(ui_world.config()) as c:
        get(c)
        get(c)
    assert digest(ui_world.files()) == before


def test_a_missing_database_is_explained_and_nothing_is_created(tmp_path: Path) -> None:
    gone = tmp_path / "nope.sqlite3"
    with serving(UiConfig(port=0)) as c:
        reply = c.get("/api/experiences")
        assert reply.status == 409 and "--db" in str(reply.json()["error"])
    with serving(UiConfig(db=gone, port=0)) as c:
        assert c.get("/api/experiences").status == 409
    assert not gone.exists()


def test_a_run_that_cannot_be_read_is_reported_and_the_rest_still_listed(tmp_path: Path) -> None:
    db = mixed_db(tmp_path)
    add_run(
        db,
        "run-bad",
        [("s", "dependencies_of", {}, success("run-bad", "s", "dependencies_of", {}))],
    )
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = 'run-bad'")
    conn.commit()
    conn.close()
    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get("/api/experiences")
        assert reply.status == 200
        data = reply.json()["data"]
        assert data["malformed"] == ["run-bad"] and data["total"] == 3
        assert any("could not be read" in w for w in reply.json()["warnings"])


def test_iter_runs_reads_each_run_once_and_marks_unreadable_ones(tmp_path: Path) -> None:
    db = mixed_db(tmp_path)
    conn = sqlite3.connect(db)
    # run-a has a failed step, so a stored status of "succeeded" contradicts it: unreadable
    conn.execute("UPDATE runs SET status = 'succeeded' WHERE run_id = 'run-a'")
    conn.commit()
    conn.close()
    with TraceStore.open_existing(db) as store:
        pairs = list(store.iter_runs())
        listing = store.list()
    assert {rid: (t is None) for rid, t in pairs} == {"run-a": True, "run-unfinished": False}
    assert listing.malformed == ("run-a",)
    assert [r.run_id for r in listing.runs] == ["run-unfinished"]


def test_the_recall_tab_is_gone_from_the_page_but_the_api_remains(ui_world: World) -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '["recall"' not in script and 'go("recall"' not in script
    assert '["experiences", "Experiences"]' in script
    with serving(ui_world.config()) as c:
        assert c.get("/api/recall?q=dependencies&min_similarity=0").status == 200
        assert json.loads(c.get("/api/status").body)["data"]["inputs"]["db"]["state"] == "present"
