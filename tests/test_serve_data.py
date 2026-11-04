"""The data and graph servers (ADR-0044): whole-database browsing, search, read-only guarantees."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.serve.cli import Servers
from serve_support import TOKEN, Models, call, config_for, running, wait_for
from test_serve_flow import get, onboard, send, status_of


@pytest.fixture
def filled(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Servers with three finished ask-mode turns, so there is something in every store."""
    config = config_for(tmp_path, Models())
    with running(config) as s:
        session = onboard(s, tmp_path)
        for text in ("what does app.py do", "explain the app module", "describe app.py once more"):
            turn = send(s, session, text, mode="ask")
            assert wait_for(lambda t=turn: status_of(s, t) == "done")  # type: ignore[misc]
            assert wait_for(lambda: not s.chat.running_turn())
        yield s, config


def data(s: Servers, path: str) -> Any:
    return call(s.data, "GET", path, token=TOKEN)


def test_overview_names_every_source_and_collection(filled: Any) -> None:
    s, _ = filled
    ov = data(s, "/api/data/overview").json
    assert {"trace", "workspace", "chat"} <= set(ov["sources"])
    names = {c["name"]: c["count"] for c in ov["collections"]}
    assert names["experiences"] >= 3 and names["decisions"] >= 3
    assert "read-only" in ov["note"]


def test_the_whole_collection_can_be_paged_through(filled: Any) -> None:
    s, _ = filled
    total = data(s, "/api/index/items?collection=decisions&limit=1").json["total"]
    seen: list[str] = []
    for offset in range(0, total, 2):
        page = data(s, f"/api/index/items?collection=decisions&offset={offset}&limit=2").json
        seen += [i["id"] for i in page["items"]]
    assert len(seen) == total == len(set(seen))
    assert data(s, "/api/index/items?collection=decisions&limit=500").status == 400
    assert data(s, "/api/index/items?collection=nope").status == 404


def test_an_item_shows_its_vector_metadata_and_re_read_source(filled: Any) -> None:
    s, _ = filled
    ids = data(s, "/api/index/items?collection=decisions&limit=50").json["items"]
    one = data(s, f"/api/index/item?collection=decisions&id={ids[0]['id']}").json
    assert one["vector"]["dimension"] == 64 and one["vector"]["norm"] > 0
    assert one["source"]["found"] and one["source"]["kind"] == "decision"
    assert one["source"]["basis"] in ("person", "model_unverified")
    steps = data(s, "/api/index/items?collection=experiences&limit=50").json["items"]
    step = data(s, "/api/index/item?collection=experiences&id=" + steps[0]["id"]).json
    assert step["source"]["found"] and step["source"]["kind"] == "step"
    assert data(s, "/api/index/item?collection=decisions&id=missing").status == 404


def test_search_by_meaning_and_nearest_records(filled: Any) -> None:
    s, _ = filled
    r = data(s, "/api/index/search?collection=experiences&q=project+file+summary&min_similarity=0")
    assert r.status == 200, r.raw
    body = r.json
    assert body["hits"] and "not evidence" in body["note"]
    first = body["hits"][0]["id"]
    near = data(s, f"/api/index/neighbours?collection=experiences&id={first}&k=3").json
    assert all(n["id"] != first for n in near["neighbours"])
    assert data(s, "/api/index/search?collection=experiences&q=").status == 400
    bad = data(s, "/api/index/search?collection=experiences&q=x&min_similarity=7")
    assert bad.status == 400


def test_projection_is_a_bounded_labelled_picture(filled: Any) -> None:
    s, _ = filled
    r = data(s, "/api/index/projection?collection=decisions&limit=3").json
    assert r["shown"] == 3 and r["total"] >= 3
    assert all(-1.0 <= p["x"] <= 1.0 and -1.0 <= p["y"] <= 1.0 for p in r["points"])
    assert "not distances in the index" in r["note"]
    assert data(s, "/api/index/projection?collection=decisions&limit=99999").status == 400


def test_tables_rows_filter_sort_and_row_detail(filled: Any) -> None:
    s, _ = filled
    tables = data(s, "/api/db/tables?source=trace").json["tables"]
    busy = max(tables, key=lambda t: t["rows"])
    assert busy["rows"] >= 3
    name, first_col = busy["name"], busy["columns"][0]
    page = data(s, f"/api/db/rows?source=trace&table={name}&limit=2").json
    assert page["total"] == busy["rows"] and len(page["rows"]) == 2
    asc = data(s, f"/api/db/rows?source=trace&table={name}&sort={first_col}&limit=50").json
    desc = data(s, f"/api/db/rows?source=trace&table={name}&sort={first_col}&desc=1&limit=50").json
    assert asc["rows"][0]["rowid"] != desc["rows"][0]["rowid"] or len(asc["rows"]) == 1
    none = data(s, f"/api/db/rows?source=trace&table={name}&q=zzz-no-such-text-zzz").json
    assert none["total"] == 0
    one = data(s, f"/api/db/row?source=trace&table={name}&rowid={page['rows'][0]['rowid']}").json
    assert [f["name"] for f in one["fields"]] == busy["columns"]


def test_unknown_names_and_sql_shaped_input_are_refused(filled: Any) -> None:
    s, _ = filled
    before = {t["name"]: t["rows"] for t in data(s, "/api/db/tables?source=trace").json["tables"]}
    assert data(s, "/api/db/tables?source=../etc").status == 404
    assert data(s, "/api/db/rows?source=trace&table=nope").status == 404
    hostile = "runs%22%3B+DROP+TABLE+runs%3B--"
    assert data(s, f"/api/db/rows?source=trace&table={hostile}").status == 404
    name = next(iter(before))
    assert data(s, f"/api/db/rows?source=trace&table={name}&sort=zzz").status == 400
    assert data(s, f"/api/db/rows?source=trace&table={name}&sort={hostile}").status == 400
    like = data(s, f"/api/db/rows?source=trace&table={name}&q=%25%27%3B+DROP").status
    assert like == 200  # a quote or % in the filter is text, never SQL
    after = {t["name"]: t["rows"] for t in data(s, "/api/db/tables?source=trace").json["tables"]}
    assert after == before


def test_browsing_leaves_every_file_byte_for_byte_alone(filled: Any) -> None:
    s, config = filled
    files = [config.workspace, config.chat]

    def digest() -> list[str]:
        return [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]

    before = digest()
    for source in ("workspace", "chat"):
        for t in data(s, f"/api/db/tables?source={source}").json["tables"]:
            data(s, f"/api/db/rows?source={source}&table={t['name']}&limit=200")
    assert digest() == before


def test_a_server_without_data_says_so_instead_of_failing(tmp_path: Path) -> None:
    with running(config_for(tmp_path)) as s:
        ov = data(s, "/api/data/overview").json
        assert ov["sources"] == {} or "trace" not in ov["sources"]
        assert ov["collections"] == [] and ov["index_note"]
        assert data(s, "/api/db/tables?source=trace").status == 404
        assert get(s.graph, "/api/workspace").status == 409


def test_graph_server_reports_impact_and_refuses_bad_names(filled: Any) -> None:
    s, _ = filled
    ok = call(s.graph, "GET", "/api/workspace/impact?project=api&target=package", token=TOKEN)
    assert ok.status == 200 and "web" in str(ok.json)
    assert (
        call(s.graph, "GET", "/api/workspace/impact?project=zzz&target=package", token=TOKEN).status
        == 404
    )
    bad = call(s.graph, "GET", "/api/workspace/impact?project=api&target=%00", token=TOKEN)
    assert bad.status == 400
    graph = get(s.graph, "/api/workspace").json
    assert {n["name"] for n in graph["nodes"]} == {"api", "web"}
    assert graph["counts"]["declared"] == 0
