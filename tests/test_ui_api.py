"""The web UI's views return what the CLI `--json` returns, read only, with explicit failures."""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh import cli
from memory_support import FakeEmbedder, add_run, success
from ui_support import A_UTIL, QUERY, UTIL, A, World, digest, run, serving


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())


def cli_json(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> Any:
    capsys.readouterr()
    assert run(*argv) == 0
    return json.loads(capsys.readouterr().out)


def test_status_lists_every_input_and_states_it_is_read_only(ui_world: World) -> None:
    with serving(ui_world.config(outcomes=None)) as c:
        reply = c.get("/api/status").json()
        data: Any = reply["data"]
        assert data["inputs"]["db"]["state"] == "present"
        assert data["inputs"]["outcomes"] == {"given": False, "state": "not given"}
        assert any("never writes" in n for n in reply["notes"])


def test_status_reports_a_missing_and_an_unusable_input_without_creating_anything(
    ui_world: World, tmp_path: Path
) -> None:
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"this is not a database")
    gone = tmp_path / "gone.sqlite3"
    before = sorted(tmp_path.iterdir())
    with serving(ui_world.config(db=junk, reviews=gone)) as c:
        data: Any = c.get("/api/status").json()["data"]
        assert data["inputs"]["db"]["state"] == "unusable"
        assert data["inputs"]["reviews"]["state"] == "missing"
    assert sorted(tmp_path.iterdir()) == before and not gone.exists()


def test_traces_and_a_trace_equal_the_cli_json(
    ui_world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = cli_json(capsys, "traces", "list", "--db", ui_world.db, "--json")
    run_id = listing["runs"][0]["run_id"]
    shown = cli_json(capsys, "traces", "show", run_id, "--db", ui_world.db, "--json")
    with serving(ui_world.config()) as c:
        assert c.get("/api/traces").json()["data"] == listing
        assert c.get("/api/trace?id=" + run_id).json()["data"] == shown


def test_claims_equal_the_cli_json_with_reviews_outcomes_and_root(
    ui_world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = ui_world
    expected = cli_json(
        capsys, "experience", "claims", "--db", w.db, "--graph", w.graph, "--root", w.app,
        "--reviews", w.reviews, "--outcomes", w.outcomes, "--json",
    )  # fmt: skip
    with serving(w.config()) as c:
        reply = c.get("/api/claims").json()
    assert reply["data"] == expected
    statuses = {r["name"]: r["evidence_status"] for r in expected["claims"]}
    assert "validated" in statuses.values() and "stale" in statuses.values()
    assert expected["status_meanings"] and expected["note"]


def test_claims_without_a_root_say_that_currency_is_unknown(
    ui_world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = ui_world
    expected = cli_json(capsys, "experience", "claims", "--db", w.db, "--graph", w.graph, "--json")
    with serving(w.config(root=None, reviews=None, outcomes=None)) as c:
        reply = c.get("/api/claims").json()
    assert reply["data"] == expected
    assert "no --root given: whether claims are current is unknown" in reply["notes"]
    assert {r["evidence_status"] for r in reply["data"]["claims"]} <= {
        "supported",
        "conflicting",
        "unsupported",
    }


def test_node_and_check_equal_the_cli_json(
    ui_world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = ui_world
    show = cli_json(
        capsys, "experience", "show", A_UTIL, "--db", w.db, "--graph", w.graph,
        "--transitive", "--json",
    )  # fmt: skip
    check = cli_json(capsys, "experience", "check", "--db", w.db, "--graph", w.graph, "--json")
    with serving(w.config()) as c:
        got = c.get("/api/node?transitive=1&id=" + A_UTIL.replace(":", "%3A"))
        assert got.json()["data"] == show
        assert c.get("/api/check").json()["data"] == check
    assert show["reached"]


def test_recall_and_search_equal_the_cli_json(
    ui_world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    w = ui_world
    recalled = cli_json(
        capsys, "recall", QUERY, "--db", w.db, "--index", w.idx, "--graph", w.graph,
        "--reviews", w.reviews, "--outcomes", w.outcomes, "--root", w.app,
        "--min-similarity", "0", "--json",
    )  # fmt: skip
    searched = cli_json(
        capsys, "memory", "search", QUERY, "--db", w.db, "--index", w.idx,
        "--min-similarity", "0", "--json",
    )  # fmt: skip
    q = "q=" + QUERY.replace(" ", "%20").replace(":", "%3A") + "&min_similarity=0"
    with serving(w.config()) as c:
        assert c.get("/api/recall?" + q).json()["data"] == recalled
        assert c.get("/api/search?" + q).json()["data"] == searched
    assert recalled["claims"] and recalled["current_fingerprint"]


def test_every_view_leaves_every_input_byte_identical(ui_world: World) -> None:
    w = ui_world
    before = digest(w.files())
    listing = sorted(p for p in w.db.parent.rglob("*"))
    q = "q=dependencies&min_similarity=0"
    with serving(w.config()) as c:
        run_id = c.get("/api/traces").json()["data"]["runs"][0]["run_id"]
        paths = ["/api/status", "/api/traces", f"/api/trace?id={run_id}", "/api/claims"]
        paths += ["/api/claims?include_unverified=1", "/api/check", "/api/recall?" + q]
        paths += ["/api/search?" + q, "/api/node?id=" + A_UTIL.replace(":", "%3A")]
        paths += ["/api/graph.html", "/", "/app.js", "/app.css"]
        for path in paths:
            assert c.get(path).status == 200, path
        assert c.get("/api/nope").status == 404
    after = digest(w.files())
    changed = sorted(k for k in before if before[k] != after.get(k))
    assert not changed, changed
    assert sorted(p for p in w.db.parent.rglob("*") if "__pycache__" not in p.parts) == [
        p for p in listing if "__pycache__" not in p.parts
    ]


def test_a_view_that_needs_an_input_that_was_not_given_says_which(ui_world: World) -> None:
    with serving(ui_world.config(db=None, graph=None, index=None, root=None)) as c:
        for path, option in [
            ("/api/traces", "--db"),
            ("/api/claims", "--graph"),
            ("/api/recall?q=x", "--graph"),
            ("/api/search?q=x", "--db"),
            ("/api/graph.html", "--root"),
        ]:
            reply = c.get(path)
            assert reply.status == 409 and option in str(reply.json()["error"]), path


@pytest.mark.parametrize(
    ("path", "status", "text"),
    [
        ("/api/trace?id=no-such-run", 404, "no run"),
        ("/api/trace", 400, "required"),
        ("/api/node?id=nope", 404, ""),
        ("/api/node", 400, "required"),
        ("/api/search?q=", 400, "required"),
        ("/api/search?q=%20%20", 400, "required"),
        ("/api/search?q=" + "a" * 2001, 400, "longer"),
        ("/api/search?q=x&k=abc", 400, "number"),
        ("/api/search?q=x&k=0", 400, "k must"),
        ("/api/search?q=x&k=51", 400, "k must"),
        ("/api/search?q=x&min_similarity=2", 400, ""),
        ("/api/search?q=x&min_similarity=nan", 400, ""),
        ("/api/recall?q=x&min_similarity=-1", 400, ""),
    ],
)
def test_bad_requests_give_explicit_messages(
    ui_world: World, path: str, status: int, text: str
) -> None:
    with serving(ui_world.config()) as c:
        reply = c.get(path)
        assert reply.status == status, reply.body
        assert text in str(reply.json()["error"])


def test_unusable_stores_are_conflicts_with_fixed_messages_and_no_paths(
    ui_world: World, tmp_path: Path
) -> None:
    bad_graph = tmp_path / "bad-graph.sqlite3"
    bad_graph.write_bytes(b"not a graph")
    missing = tmp_path / "missing"
    cases = [
        ("/api/claims", ui_world.config(graph=bad_graph)),
        ("/api/traces", ui_world.config(db=missing / "t.sqlite3")),
        ("/api/search?q=x", ui_world.config(index=missing)),
        ("/api/claims", ui_world.config(reviews=missing / "r.sqlite3")),
        ("/api/recall?q=x", ui_world.config(outcomes=missing / "o.sqlite3")),
        ("/api/graph.html", ui_world.config(root=missing)),
    ]
    for path, config in cases:
        with serving(config) as c:
            reply = c.get(path)
            assert reply.status == 409, (path, reply.body)
            if path != "/api/graph.html":  # ingestion names the --root the user gave
                assert str(tmp_path) not in str(reply.json()["error"])
    assert not missing.exists()


def test_malformed_run_rows_are_a_warning_not_a_failure(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(
        db, "run-ok", [("s", "dependencies_of", {}, success("run-ok", "s", "dependencies_of", {}))]
    )
    add_run(
        db,
        "run-bad",
        [("s", "dependencies_of", {}, success("run-bad", "s", "dependencies_of", {}))],
    )
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = 'run-bad'")
    conn.commit()
    conn.close()
    from cognitive_mesh.ui.config import UiConfig

    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get("/api/traces").json()
        assert [r["run_id"] for r in reply["data"]["runs"]] == ["run-ok"]
        assert reply["data"]["malformed"] == ["run-bad"]
        assert any("malformed" in w for w in reply["warnings"])


def test_hostile_trace_text_comes_back_as_inert_json_data(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    out: Any = {"note": evil}
    add_run(
        db,
        "run-evil",
        [("s", "dependencies_of", {}, success("run-evil", "s", "dependencies_of", out))],
    )
    from cognitive_mesh.ui.config import UiConfig

    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get("/api/trace?id=run-evil")
        assert reply.headers["content-type"] == "application/json"
        assert reply.headers["x-content-type-options"] == "nosniff"
        assert evil in json.dumps(reply.json())
        assert b"<script>" in reply.body  # present only as a JSON string value


def test_the_graph_page_is_the_escaped_adr_0008_page(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        reply = c.get("/api/graph.html")
        assert reply.status == 200 and reply.headers["content-type"].startswith("text/html")
        assert b"Dependency graph" in reply.body and b"<script" not in reply.body
        assert A.encode() in reply.body or b"a.py" in reply.body
        assert UTIL.encode() in reply.body or b"util.py" in reply.body


def test_parallel_recalls_load_the_embedding_model_once(ui_world: World) -> None:
    built: list[int] = []

    def factory() -> FakeEmbedder:
        built.append(1)
        return FakeEmbedder()

    results: list[int] = []

    def one(c: Any) -> None:
        results.append(c.get("/api/recall?q=dependencies&min_similarity=0").status)

    with serving(ui_world.config(embedder_factory=factory)) as c:
        threads = [threading.Thread(target=one, args=(c,)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
    assert results == [200] * 6 and len(built) == 1
