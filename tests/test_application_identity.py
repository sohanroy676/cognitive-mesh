"""Application identity (ADR-0021): derivation, per-application claims, the gate, old files."""

import json
import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.experience import ExperienceGraph, build_from_traces
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.assess import _contradiction_candidates, claim_parts
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.models import FORMAT_VERSION, ExperienceGraphError, NodeKind
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.identity import IdentityError, IdentityHow, derive_identity
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.retrieval import GatedContext, build_context, recall
from cognitive_mesh.retrieval.context import REASON_OTHER_APPLICATION, REASON_OTHER_DIRECTORY
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db, model_step
from test_recall import Scripted

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
FP = "1" * 64


def test_a_declared_id_wins_and_is_labelled(tmp_path: Path) -> None:
    got = derive_identity(str(tmp_path / "shop"), "my-shop")
    assert got is not None and (got.app_id, got.how) == ("my-shop", IdentityHow.DECLARED)


@pytest.mark.parametrize("bad", ["", " ", "a b", "x" * 101, "-lead", "semi;colon", "new\nline"])
def test_a_bad_declared_id_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(IdentityError):
        derive_identity(str(tmp_path), bad)


def test_a_manifest_name_and_the_directory_make_a_manifest_id() -> None:
    got = derive_identity(str(FIXTURE))
    assert got is not None and (got.app_id, got.how) == ("dep-app@dep_app", IdentityHow.MANIFEST)
    assert not got.weak


def test_without_a_manifest_the_directory_name_is_a_weak_id(tmp_path: Path) -> None:
    root = tmp_path / "plain_app"
    root.mkdir()
    got = derive_identity(str(root))
    assert got is not None and (got.app_id, got.how) == ("plain_app", IdentityHow.DIRECTORY)
    assert got.weak


@pytest.mark.parametrize("manifest", ["pyproject.toml", "package.json"], ids=["toml", "json"])
@pytest.mark.parametrize(
    "text", ["{{{ not valid", "[project]\nname = 5\n", '{"name": ["x"]}', "\x00"]
)
def test_a_broken_manifest_falls_back_to_the_directory(
    tmp_path: Path, manifest: str, text: str
) -> None:
    root = tmp_path / "broken_app"
    root.mkdir()
    (root / manifest).write_text(text, newline="\n")
    got = derive_identity(str(root))
    assert got is not None and got.how is IdentityHow.DIRECTORY


def test_a_manifest_name_that_is_not_a_clean_label_is_not_used(tmp_path: Path) -> None:
    root = tmp_path / "odd_app"
    root.mkdir()
    (root / "package.json").write_text('{"name": "bad name; rm -rf"}', newline="\n")
    got = derive_identity(str(root))
    assert got is not None and got.how is IdentityHow.DIRECTORY


def graph_for(tmp_path: Path, apps: dict[str, str]) -> tuple[ExperienceGraph, Path]:
    runs = {r: ([deps_step(r, "s1", A, [B])], FP) for r in apps}
    db = make_db(tmp_path, runs, apps=apps)
    with TraceStore.open_existing(db) as store:
        return ExperienceGraph(build_from_traces(store)), db


def claims_of(graph: ExperienceGraph) -> list[str]:
    return [n.id for n in graph.nodes(NodeKind.CLAIM)]


def test_equal_node_ids_from_different_applications_stay_separate_claims(tmp_path: Path) -> None:
    graph, _ = graph_for(tmp_path, {"r1": "shop-one", "r2": "shop-two"})
    ids = claims_of(graph)
    assert sorted(ids) == sorted(
        [
            claim_id("depends_on", A, B, "imports", "shop-one"),
            claim_id("depends_on", A, B, "imports", "shop-two"),
        ]
    )
    assert {graph.node(i).app_id for i in ids} == {"shop-one", "shop-two"}


def test_the_same_application_across_runs_is_one_claim(tmp_path: Path) -> None:
    graph, _ = graph_for(tmp_path, {"r1": "shop-one", "r2": "shop-one"})
    assert claims_of(graph) == [claim_id("depends_on", A, B, "imports", "shop-one")]


def test_runs_without_an_id_keep_their_old_claim_id_and_stay_separate(tmp_path: Path) -> None:
    runs = {
        "r1": ([deps_step("r1", "s1", A, [B])], FP),
        "r2": ([deps_step("r2", "s1", A, [B])], FP),
    }
    db = make_db(tmp_path, runs, apps={"r2": "shop-one"})
    with TraceStore.open_existing(db) as store:
        graph = ExperienceGraph(build_from_traces(store))
    assert sorted(claims_of(graph)) == sorted(
        [
            claim_id("depends_on", A, B, "imports"),
            claim_id("depends_on", A, B, "imports", "shop-one"),
        ]
    )
    assert claim_id("depends_on", A, B, "imports") != claim_id("depends_on", A, B, "imports", "")


def test_another_applications_complete_listing_does_not_contradict(tmp_path: Path) -> None:
    runs = {
        "r1": ([deps_step("r1", "s1", A, [B])], FP),
        "r2": ([deps_step("r2", "s1", A, [])], FP),  # a complete listing without the claim
    }
    db = make_db(tmp_path, runs, apps={"r1": "shop-one", "r2": "shop-two"})
    with TraceStore.open_existing(db) as store:
        graph = ExperienceGraph(build_from_traces(store))
    claim = graph.node(claim_id("depends_on", A, B, "imports", "shop-one"))
    subject, _ = claim_parts(graph, claim)
    assert _contradiction_candidates(graph, claim, subject) == ["attempt:r1/s1/1"]


def test_the_graph_file_round_trips_the_ids_and_older_formats_are_refused(tmp_path: Path) -> None:
    _, db = graph_for(tmp_path, {"r1": "shop-one"})
    with TraceStore.open_existing(db) as store:
        snapshot = build_from_traces(store)
    path = tmp_path / "g.sqlite3"
    graph_store.save(path, snapshot)
    loaded = graph_store.load(path)
    assert {n.app_id for n in loaded.snapshot.nodes if n.kind is NodeKind.CLAIM} == {"shop-one"}
    (run,) = [n for n in loaded.snapshot.nodes if n.kind is NodeKind.RUN]
    assert (run.app_id, run.app_id_how) == ("shop-one", IdentityHow.DECLARED)
    assert FORMAT_VERSION == 5
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '3' WHERE key = 'format_version'")
    conn.commit()
    conn.close()
    with pytest.raises(ExperienceGraphError, match="experience build"):
        graph_store.load(path)


def gated(
    tmp_path: Path, apps: dict[str, str], here_id: str | None, roots: dict[str, str]
) -> GatedContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runs = {r: ([deps_step(r, "s1", A, [B])], FP) for r in ("r1", "r2")}
    runs["r3"] = ([model_step("r3", "s1")], FP)
    db = make_db(tmp_path, runs, roots, apps)
    from cognitive_mesh.identity import AppIdentity

    identity = AppIdentity(app_id=here_id, how=IdentityHow.DECLARED) if here_id else None
    with TraceStore.open_existing(db) as store:
        snap = build_from_traces(store)
        hits = Scripted([("r1", "s1", 1, 0.9), ("r2", "s1", 1, 0.8), ("r3", "s1", 1, 0.7)])
        result = recall("q", hits, store, snap, k=5, min_similarity=0.2)
        graph = build_dependency_graph(ingest_application(FIXTURE))
        return build_context(
            "q", result, ExperienceGraph(snap), graph, store, "/work/dep_app", identity
        )


def test_the_gate_keeps_only_the_matching_application(tmp_path: Path) -> None:
    roots = {r: "/elsewhere/dep_app" for r in ("r1", "r2", "r3")}
    got = gated(tmp_path, {"r1": "shop-one", "r2": "shop-two", "r3": "shop-one"}, "shop-one", roots)
    (claim,) = got.pack.claims
    assert claim.identity == "matched" and claim.supporting[0].run_id == "r1"
    (raw,) = got.pack.raw
    assert raw.identity == "matched" and raw.run_id == "r3"
    assert [n.reason for n in got.not_applicable] == [REASON_OTHER_APPLICATION]


def test_unrecorded_runs_keep_the_old_gate_and_are_labelled(tmp_path: Path) -> None:
    same = gated(tmp_path / "a", {}, "shop-one", {r: "C:\\x\\dep_app" for r in ("r1", "r2", "r3")})
    assert same.pack.claims and all(c.identity == "unrecorded" for c in same.pack.claims)
    assert [r.identity for r in same.pack.raw] == ["unrecorded"]
    other = gated(tmp_path / "b", {}, "shop-one", {r: "C:\\x\\another" for r in ("r1", "r2", "r3")})
    assert other.pack.raw == ()
    assert REASON_OTHER_DIRECTORY in {n.reason for n in other.not_applicable}


def test_without_a_current_id_nothing_is_matched(tmp_path: Path) -> None:
    got = gated(
        tmp_path,
        {"r1": "shop-one", "r2": "shop-two", "r3": "shop-one"},
        None,
        {r: "/y/dep_app" for r in ("r1", "r2", "r3")},
    )
    assert got.pack.claims and all(c.identity == "unrecorded" for c in got.pack.claims)
    assert json.dumps(got.pack.model_dump(mode="json"))


def test_a_bad_app_id_exits_2_and_records_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cognitive_mesh.cli import main

    db = tmp_path / "t.sqlite3"
    plan = str(Path(__file__).parent / "fixtures" / "plans" / "ok.json")
    argv = ["run", plan, "--root", str(FIXTURE), "--trace-db", str(db), "--app-id", "bad id;"]
    assert main(argv) == 2
    assert "application id" in capsys.readouterr().err
    assert not db.exists()


def test_traces_show_prints_the_id_and_its_origin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cognitive_mesh.cli import main

    db = tmp_path / "t.sqlite3"
    plan = str(Path(__file__).parent / "fixtures" / "plans" / "ok.json")
    assert main(["run", plan, "--root", str(FIXTURE), "--trace-db", str(db)]) == 0
    run_id = capsys.readouterr().out.split("trace: ")[1].split()[0]
    assert main(["traces", "show", run_id, "--db", str(db)]) == 0
    assert "application id: dep-app@dep_app [manifest]" in capsys.readouterr().out
