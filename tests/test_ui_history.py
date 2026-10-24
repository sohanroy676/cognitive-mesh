"""Every run that touched a node or a claim (ADR-0032 point 6)."""

import shutil
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.experience import history
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.ui.config import UiConfig
from ui_support import A_UTIL, APP_ID, UTIL, A, World, digest, serving

B = "module:src/dep_app/b.py"
A_B = claim_id("depends_on", A, B, "imports", APP_ID)


def history_url(node: str) -> str:
    return "/api/history?" + urllib.parse.urlencode({"id": node})


def rows_for(c: Any, node: str) -> dict[str, Any]:
    reply = c.get(history_url(node))
    assert reply.status == 200, reply.body
    data: dict[str, Any] = reply.json()["data"]
    return data


def test_a_claim_lists_the_runs_that_support_it_and_the_runs_that_contradict_it(
    ui_world: World,
) -> None:
    with serving(ui_world.config()) as c:
        new = rows_for(c, A_UTIL)
        old = rows_for(c, A_B)
    assert new["found"] and new["node"]["kind"] == "claim"
    assert [r["role"] for r in new["rows"]].count("supports") == 1
    contradicting = [r for r in new["rows"] if r["role"] == "contradicts"]
    assert len(contradicting) == 2 and all(r["same_version"] is False for r in contradicting)
    assert [r["role"] for r in old["rows"]].count("supports") == 2
    old_against = [r for r in old["rows"] if r["role"] == "contradicts"]
    assert len(old_against) == 1 and old_against[0]["same_version"] is False
    assert new["status"] == "validated" and old["status"] == "stale"
    for r in new["rows"] + old["rows"]:
        assert r["source_check"] == "ok" and r["run_status"] == "succeeded"
        assert r["code_version"] and r["started_at"] and r["capability"] == "dependencies_of"


def test_rows_are_newest_first_and_every_run_is_counted_once(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        data = rows_for(c, A_B)
    stamps = [r["started_at"] for r in data["rows"]]
    assert stamps == sorted(stamps, reverse=True)
    assert data["total_runs"] == len({r["run_id"] for r in data["rows"]}) == 3
    assert data["truncated"] is False


def test_a_dependency_graph_id_finds_its_target_and_the_claims_about_it(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        plain = rows_for(c, A)
        exact = rows_for(c, f"target:{A}")
    assert plain["node"]["id"] == f"target:{A}" == exact["node"]["id"]
    assert plain["rows"] and all(r["role"] == "ran" for r in plain["rows"])
    names = " ".join(cl["name"] for cl in plain["claims"])
    assert UTIL in names and B in names
    assert plain["rows"] == exact["rows"]


def test_a_run_id_lists_the_attempts_of_that_run(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        run_id = c.get("/api/traces").json()["data"]["runs"][0]["run_id"]
        data = rows_for(c, f"run:{run_id}")
    assert data["found"] and data["rows"] and {r["run_id"] for r in data["rows"]} == {run_id}


def test_an_id_no_run_mentions_is_an_empty_answer_not_an_error(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        reply = c.get(history_url("module:src/nothing/here.py"))
    assert reply.status == 200
    data = reply.json()["data"]
    assert data["found"] is False and data["rows"] == [] and data["claims"] == []
    assert "not validated knowledge" in data["note"]


@pytest.mark.parametrize(("option", "field"), [("--graph", "graph"), ("--db", "db")])
def test_history_says_which_input_is_missing(ui_world: World, option: str, field: str) -> None:
    with serving(ui_world.config(**{field: None})) as c:
        reply = c.get(history_url(A_UTIL))
        assert reply.status == 409 and option in str(reply.json()["error"])
    with serving(ui_world.config()) as c:
        assert c.get("/api/history").status == 400  # the id is required


def test_a_row_that_no_longer_matches_its_trace_is_shown_and_marked(
    ui_world: World, tmp_path: Path
) -> None:
    db = tmp_path / "t.sqlite3"
    shutil.copy(ui_world.db, db)
    with serving(ui_world.config(db=db)) as c:
        victim = rows_for(c, A_UTIL)["rows"][0]["run_id"]
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs WHERE run_id = ?", (victim,))
    conn.commit()
    conn.close()
    with serving(ui_world.config(db=db)) as c:
        reply = c.get(history_url(A_UTIL))
        assert reply.status == 200
        gone = [r for r in reply.json()["data"]["rows"] if r["run_id"] == victim]
        assert gone
        assert all(r["source_check"] != "ok" and r["run_status"] == "unreadable" for r in gone)
        assert any("not confirmed" in w for w in reply.json()["warnings"])


def test_a_long_history_is_cut_visibly(ui_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history, "MAX_RUNS", 1)
    with serving(ui_world.config()) as c:
        data = rows_for(c, A_B)
    assert len({r["run_id"] for r in data["rows"]}) == 1
    assert data["total_runs"] == 3 and data["truncated"] is True


def test_history_writes_nothing(ui_world: World) -> None:
    before = digest(ui_world.files())
    with serving(ui_world.config()) as c:
        for node in (A_UTIL, A_B, A, "nope"):
            c.get(history_url(node))
    assert digest(ui_world.files()) == before


def test_history_works_without_a_root_or_review_files(ui_world: World) -> None:
    config = UiConfig(db=ui_world.db, graph=ui_world.graph, port=0)
    with serving(config) as c:
        data = rows_for(c, A_UTIL)
    assert data["status"] in {"supported", "conflicting"} and data["rows"]
