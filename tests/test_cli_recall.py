"""`recall` and `experience review` end to end (ADR-0016): real runs, real index, fake embedder."""

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import main
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.reviews import ReviewStore, Verdict
from memory_support import FakeEmbedder

FIXTURES = Path(__file__).parent / "fixtures"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
UTIL = "module:src/dep_app/util.py"
A_B = claim_id("depends_on", A, B, "imports")
A_UTIL = claim_id("depends_on", A, UTIL, "imports")
QUERY = "dependencies_of module:src/dep_app/a.py"


@dataclass
class World:
    app: Path
    db: Path
    idx: Path
    graph: Path
    reviews: Path


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_make_embedder", lambda: FakeEmbedder())


def run(*argv: str | Path) -> int:
    return main([str(a) for a in argv])


def out_of(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    got = capsys.readouterr()
    return got.out, got.err


@pytest.fixture
def world(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> World:
    """Two runs on the original code, an edit (a.py no longer imports b), one more run."""
    app = tmp_path / "app"
    shutil.copytree(FIXTURES / "dep_app", app)
    w = World(
        app, tmp_path / "t.sqlite3", tmp_path / "idx", tmp_path / "g.sqlite3", tmp_path / "r.db"
    )
    plan = FIXTURES / "plans" / "ok.json"
    for _ in range(2):
        assert run("run", plan, "--root", app, "--trace-db", w.db) == 0
    a = app / "src" / "dep_app" / "a.py"
    a.write_text(a.read_text().replace("from . import b", "from . import util"), newline="\n")
    assert run("run", plan, "--root", app, "--trace-db", w.db) == 0
    assert run("memory", "index", "--db", w.db, "--index", w.idx) == 0
    assert run("experience", "build", "--db", w.db, "--graph", w.graph) == 0
    capsys.readouterr()
    return w


def recall(w: World, *extra: str | Path, query: str = QUERY) -> list[str | Path]:
    return ["recall", query, "--db", w.db, "--index", w.idx, "--graph", w.graph, *extra]


def recall_json(
    w: World, capsys: pytest.CaptureFixture[str], *extra: str | Path
) -> dict[str, object]:
    assert run(*recall(w, "--min-similarity", "0", "--json", *extra)) == 0
    data: dict[str, object] = json.loads(out_of(capsys)[0])
    return data


def claim_in(data: dict[str, object], cid: str) -> dict[str, object]:
    claims = data["claims"]
    assert isinstance(claims, list)
    found = [c for c in claims if c["assessment"]["claim_id"] == cid]
    assert len(found) == 1, cid
    item: dict[str, object] = found[0]
    return item


def status_of(item: dict[str, object]) -> object:
    assessment = item["assessment"]
    assert isinstance(assessment, dict)
    return assessment["status"]


def test_recall_without_a_root_reports_support_and_unknown_currency(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(*recall(world, "--min-similarity", "0")) == 0
    out, _ = out_of(capsys)
    assert f"[supported] {A} depends_on {B}" in out
    assert "no --root given: whether claims are current is unknown" in out
    assert "contradicted by" in out and "other or unrecorded version" in out
    assert "not evidence that a past result was correct" in out
    assert "not proof that the claim is true" in out


def test_recall_against_the_edited_code_separates_stale_from_validated(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    data = recall_json(world, capsys, "--root", world.app)
    assert status_of(claim_in(data, A_B)) == "stale"
    assert status_of(claim_in(data, A_UTIL)) == "validated"
    validated = claim_in(data, A_UTIL)["assessment"]
    assert isinstance(validated, dict) and validated["validated_by"] == ["rederived"]
    assert validated["rederivation"]["evidence"]  # path, hash, line, rule
    assert data["current_fingerprint"]


def test_both_sides_of_a_review_conflict_are_listed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    review = ["experience", "review", A_UTIL, "--verdict", "rejected", "--note", "I disagree"]
    code = run(
        *review, "--by", "alice", "--root", world.app, "--graph", world.graph,
        "--reviews", world.reviews,
    )  # fmt: skip
    assert code == 0 and "unauthenticated" in out_of(capsys)[0]
    data = recall_json(world, capsys, "--root", world.app, "--reviews", world.reviews)
    item = claim_in(data, A_UTIL)["assessment"]
    assert isinstance(item, dict) and item["status"] == "conflicting"
    assert item["supporting"] and item["reviews"][0]["verdict"] == "rejected"
    assert item["validated_by"] == []  # a conflict is never reported as validated


def test_recall_needs_no_model_provider_or_config(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "bogus")
    assert run(*recall(world, "--min-similarity", "0")) == 0


def test_an_unrelated_query_finds_nothing_and_says_so(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(*recall(world, "--min-similarity", "0.9", query="zebra quantum")) == 0
    assert "no sufficiently similar experience" in out_of(capsys)[0]


def test_a_run_newer_than_the_graph_is_not_confirmed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = FIXTURES / "plans" / "ok.json"
    assert run("run", plan, "--root", world.app, "--trace-db", world.db) == 0
    assert run("memory", "index", "--db", world.db, "--index", world.idx) == 0
    out_of(capsys)
    data = recall_json(world, capsys)
    reasons = {u["reason"] for u in data["unconfirmed"]}  # type: ignore[attr-defined]
    assert "not_in_graph" in reasons


def test_deleting_a_run_makes_its_claims_unconfirmed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(world.db)
    conn.execute("DELETE FROM runs")  # every run is gone; the index and graph remember them
    conn.commit()
    conn.close()
    data = recall_json(world, capsys)
    assert data["claims"] == []
    assert {u["reason"] for u in data["unconfirmed"]} == {"source_missing"}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("tweak", "code"),
    [
        ("graph", 12),
        ("reviews", 15),
        ("root", 3),
        ("index", 11),
        ("db", 8),
    ],
)
def test_failures_have_their_own_exit_codes(
    world: World, tweak: str, code: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope"
    argv = recall(world)
    if tweak == "graph":
        argv[argv.index("--graph") + 1] = missing
    elif tweak == "reviews":
        argv += ["--reviews", missing]
    elif tweak == "root":
        argv += ["--root", missing]
    elif tweak == "index":
        argv[argv.index("--index") + 1] = missing
    else:
        argv[argv.index("--db") + 1] = missing
    assert run(*argv) == code
    assert out_of(capsys)[1]
    assert not missing.exists()


def test_a_format_1_graph_file_is_refused_with_a_rebuild_hint(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(world.graph)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'format_version'")
    conn.commit()
    conn.close()
    assert run(*recall(world)) == 12
    assert "experience build" in out_of(capsys)[1]


def test_review_of_an_unknown_or_non_claim_node_is_exit_13(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    for node in ("claim:" + "0" * 32, "run:" + sqlite_run_id(world.db)):
        code = run(
            "experience", "review", node, "--verdict", "confirmed", "--note", "n", "--by", "a",
            "--root", world.app, "--graph", world.graph, "--reviews", world.reviews,
        )  # fmt: skip
        assert code == 13
    assert not world.reviews.exists()  # nothing recorded


def sqlite_run_id(db: Path) -> str:
    conn = sqlite3.connect(db)
    try:
        return str(conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def test_a_bad_review_is_refused_and_stores_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        "experience", "review", A_B, "--verdict", "confirmed", "--note", "bad\x1b[31m", "--by",
        "a", "--root", world.app, "--graph", world.graph, "--reviews", world.reviews,
    )  # fmt: skip
    assert code == 15
    with ReviewStore.open_existing(world.reviews) as store:
        assert store.list_reviews() == []


def test_a_secret_in_a_note_never_reaches_the_file_or_the_output(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "gsk_" + "a1B2c3D4" * 4  # assembled at runtime
    code = run(
        "experience", "review", A_UTIL, "--verdict", "confirmed", "--note", f"key {secret}",
        "--by", "a", "--root", world.app, "--graph", world.graph, "--reviews", world.reviews,
    )  # fmt: skip
    assert code == 0
    assert run("experience", "claims", "--db", world.db, "--graph", world.graph,
               "--reviews", world.reviews, "--root", world.app) == 0  # fmt: skip
    out, err = out_of(capsys)
    assert secret not in out + err and secret.encode() not in world.reviews.read_bytes()


def test_claims_show_evidence_status_and_orphaned_reviews(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    with ReviewStore.create(world.reviews) as store:
        store.add("claim:" + "9" * 32, "a" * 64, Verdict.CONFIRMED, "n", "bob")
    args = ("experience", "claims", "--db", world.db, "--graph", world.graph)
    assert run(*args, "--reviews", world.reviews, "--root", world.app) == 0
    out, _ = out_of(capsys)
    assert "[validated]" in out and "[stale]" in out
    assert "orphaned review #1" in out
    assert run(*args, "--json") == 0
    data = json.loads(out_of(capsys)[0])
    assert {c["evidence_status"] for c in data["claims"]} <= {"supported", "conflicting"}
    assert data["current_fingerprint"] is None and data["status_meanings"]
