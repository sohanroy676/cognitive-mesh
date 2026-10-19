"""Shared and scoped assessment and source checks (ADR-0028): results must not change."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from cognitive_mesh.experience import (
    ExperienceGraph,
    Snapshot,
    SourceStatus,
    build_from_traces,
    check_sources,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.assess import ClaimAssessment, assess_claim, assess_claims
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource, Holding, Rederivation
from cognitive_mesh.experience.reviews import Review, Verdict
from cognitive_mesh.experience.verify import SourceReport, check_nodes
from cognitive_mesh.traces import RunTrace, TraceStore
from experience_support import deps_step, make_db
from memory_support import add_run

A, B, C, D = "module:a.py", "module:b.py", "module:c.py", "module:d.py"
FP1, FP2, FP3 = "1" * 64, "2" * 64, "3" * 64
AC = claim_id("depends_on", A, C, "imports")
AD = claim_id("depends_on", A, D, "imports")


class Stub(CurrentSource):
    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint

    def check(
        self, predicate: str, subject: str, obj: str | None, detail: str | None
    ) -> Rederivation:
        return Rederivation(holding=Holding.SUBJECT_ABSENT)


def build(tmp_path: Path) -> tuple[Path, Snapshot]:
    """Three code versions list module a; no version states every claim."""
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B, C])], FP1),
            "r2": ([deps_step("r2", "s1", A, [B])], FP2),
            "r3": ([deps_step("r3", "s1", A, [B, D])], FP3),
            "r4": ([deps_step("r4", "s1", B, [C])], FP1),
        },
    )
    with TraceStore.open_existing(db) as store:
        return db, build_from_traces(store)


def sql(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def review(claim: str, fp: str, verdict: Verdict, n: int) -> Review:
    return Review(
        id=n,
        claim_id=claim,
        fingerprint=fp,
        verdict=verdict,
        note="n",
        reviewer="r",
        created_at=datetime(2026, 10, 19, tzinfo=UTC),
    )


def damage(db: Path) -> None:
    """r2 gone (missing), r3 edited (changed), r4 malformed (unreadable); r1 stays fine."""
    sql(
        db,
        "DELETE FROM attempts WHERE run_id = 'r2'",
        "DELETE FROM steps WHERE run_id = 'r2'",
        "DELETE FROM runs WHERE run_id = 'r2'",
        "UPDATE attempts SET result_json = replace(result_json, 'module:d.py', 'module:z.py')"
        " WHERE run_id = 'r3'",
        "UPDATE runs SET status = 'failed' WHERE run_id = 'r4'",
    )


# -- check_nodes equals check_sources -----------------------------------------------------------


@pytest.mark.parametrize("damaged", [False, True])
def test_check_nodes_gives_the_whole_graph_status_for_every_node(
    tmp_path: Path, damaged: bool
) -> None:
    db, snap = build(tmp_path)
    if damaged:
        damage(db)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        whole = check_sources(snap, store).statuses
        assert len(set(whole.values())) > (2 if damaged else 0)  # the damage shows up
        assert check_nodes(graph, store, list(whole)) == whole
        for node_id in whole:  # each node alone, with no help from a shared cache
            assert check_nodes(graph, store, [node_id]) == {node_id: whole[node_id]}


def test_check_nodes_reads_only_what_the_node_rests_on(tmp_path: Path) -> None:
    db, snap = build(tmp_path)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        reads: list[str] = []
        real = store.get

        def spy(run_id: str) -> RunTrace:
            reads.append(run_id)
            return real(run_id)

        store.get = spy  # type: ignore[method-assign]
        check_nodes(graph, store, ["attempt:r1/s1/1"])
    assert set(reads) == {"r1"}


def test_check_nodes_judges_a_run_by_its_documents_like_the_whole_graph_check(
    tmp_path: Path,
) -> None:
    """r1 is re-recorded as unfinished: readable, but it no longer yields any document."""
    db, snap = build(tmp_path)
    sql(
        db,
        "DELETE FROM attempts WHERE run_id = 'r1'",
        "DELETE FROM steps WHERE run_id = 'r1'",
        "DELETE FROM runs WHERE run_id = 'r1'",
    )
    add_run(db, "r1", [deps_step("r1", "s1", A, [B, C])], fingerprint=FP1, finish=False)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        whole = check_sources(snap, store).statuses
        assert whole["run:r1"] is SourceStatus.CHANGED
        assert check_nodes(graph, store, ["run:r1"]) == {"run:r1": SourceStatus.CHANGED}
        assert check_nodes(graph, store, list(whole)) == whole


def test_check_nodes_rejects_an_unknown_node(tmp_path: Path) -> None:
    db, snap = build(tmp_path)
    with TraceStore.open_existing(db) as store, pytest.raises(Exception, match="unknown node"):
        check_nodes(ExperienceGraph(snap), store, ["nope"])


# -- shared listings do not change any assessment -----------------------------------------------


def alone(
    graph: ExperienceGraph,
    report: SourceReport,
    claim_id_: str,
    reviews: list[Review],
    current: CurrentSource | None,
) -> ClaimAssessment:
    """The assessment with nothing shared: a fresh call for this one claim."""
    mine = [r for r in reviews if r.claim_id == claim_id_]
    return assess_claim(graph, graph.node(claim_id_), report, mine, current)


@pytest.mark.parametrize("damaged", [False, True])
def test_shared_assessment_equals_claim_by_claim_assessment(tmp_path: Path, damaged: bool) -> None:
    db, snap = build(tmp_path)
    if damaged:
        damage(db)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    # a confirmation on FP2 makes r2's listing "same version" for AC but not for AD
    reviews = [review(AC, FP2, Verdict.CONFIRMED, 1), review(AD, FP1, Verdict.REJECTED, 2)]
    for current in (None, Stub(FP2)):
        got = assess_claims(graph, report, reviews, current)
        assert set(got) == {c.id for c in graph.claims()}
        for cid, assessment in got.items():
            expected = alone(graph, report, cid, reviews, current)
            assert assessment.model_dump_json() == expected.model_dump_json(), cid


def test_one_listing_is_same_version_for_one_claim_and_not_for_another(tmp_path: Path) -> None:
    db, snap = build(tmp_path)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    got = assess_claims(graph, report, [review(AC, FP2, Verdict.CONFIRMED, 1)], None)
    ac = {c.attempt_id: c for c in got[AC].contradicting}
    ad = {c.attempt_id: c for c in got[AD].contradicting}
    assert ac["attempt:r2/s1/1"].same_version is True  # FP2 is confirmed for AC
    assert ad["attempt:r2/s1/1"].same_version is False
    assert "attempt:r1/s1/1" not in ac  # AC's own evidence never contradicts it
    assert "attempt:r1/s1/1" in ad  # but r1 lacks D, so it does contradict AD
    assert ac["attempt:r2/s1/1"].run_id == "r2" and ac["attempt:r2/s1/1"].fingerprint == FP2


def test_a_listing_with_a_missing_source_never_contradicts(tmp_path: Path) -> None:
    db, snap = build(tmp_path)
    damage(db)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    got = assess_claims(graph, report, [], None)
    assert {c.run_id for c in got[AC].contradicting} <= {"r1", "r3"}
    assert all(c.run_id != "r2" for c in got[AD].contradicting)  # r2 is gone


def test_only_still_scopes_the_shared_assessment(tmp_path: Path) -> None:
    db, snap = build(tmp_path)
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    whole = assess_claims(graph, report, [], None)
    scoped = assess_claims(graph, report, [], None, only={AD})
    assert set(scoped) == {AD}
    assert scoped[AD].model_dump_json() == whole[AD].model_dump_json()


# -- the commands that were scoped give what the whole-graph path gives -------------------------


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    db, _ = build(tmp_path)
    paths = {"db": db, "graph": tmp_path / "g.sqlite3", "o": tmp_path / "o.sqlite3"}
    assert main(["experience", "build", "--db", str(db), "--graph", str(paths["graph"])]) == 0
    return paths


def cli(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    got = capsys.readouterr()
    return code, got.out, got.err


def test_show_reports_the_same_statuses_as_the_whole_graph_check(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    damage(world["db"])
    code, out, err = cli(
        capsys, "experience", "show", AC, "--db", world["db"], "--graph", world["graph"],
        "--transitive", "--include-unverified", "--json",
    )  # fmt: skip
    assert code == 0, err
    shown = json.loads(out)
    snapshot = graph_store.load(world["graph"]).snapshot
    with TraceStore.open_existing(world["db"]) as store:
        whole = check_sources(snapshot, store).statuses
    assert shown["status"] == whole[AC].value
    assert shown["reached"]
    for row in shown["reached"]:
        assert row["status"] == whole[row["node"]["id"]].value


def test_show_still_fails_the_same_way(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing_db = tmp_path / "nope.sqlite3"
    code, _, _ = cli(
        capsys, "experience", "show", AC, "--db", missing_db, "--graph", world["graph"]
    )
    assert code == 8
    code, _, err = cli(
        capsys, "experience", "show", "nope", "--db", world["db"], "--graph", world["graph"]
    )
    assert code != 0 and "unknown node" in err


def test_promote_judges_the_claim_as_the_whole_graph_does(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    damage(world["db"])
    blank = tmp_path / "blank"
    blank.mkdir()
    code, out, err = cli(
        capsys, "experience", "claims", "--db", world["db"], "--graph", world["graph"],
        "--root", blank, "--include-unverified", "--json",
    )  # fmt: skip
    assert code == 0, err
    whole = {r["id"]: r["evidence_status"] for r in json.loads(out)["claims"]}
    for cid in (AC, AD):
        code, out, err = cli(
            capsys, "experience", "promote", cid, "--by", "ann", "--db", world["db"],
            "--graph", world["graph"], "--root", blank, "--outcomes", world["o"], "--json",
        )  # fmt: skip
        assert code != 0, err  # nothing validates a claim against an empty directory
        assert json.loads(out)["status"] == whole[cid]


def test_promote_refuses_a_node_that_is_not_a_claim(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, _, err = cli(
        capsys, "experience", "promote", "run:r1", "--by", "ann", "--db", world["db"],
        "--graph", world["graph"], "--root", tmp_path, "--outcomes", world["o"],
    )  # fmt: skip
    assert code != 0 and "not a claim" in err
