"""Selection order, conflict-safe limits and profiles of the context pack (ADR-0023)."""

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from cognitive_mesh.agents.code_explainer import MAX_PRIOR_CONTEXT_CHARS, prior_context_for_prompt
from cognitive_mesh.agents.context import MAX_CLAIMS, ClaimItem, ContextProfile, RecalledContext
from cognitive_mesh.experience import Snapshot, build_from_traces
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.retrieval import GatedContext, build_context, recall
from cognitive_mesh.retrieval.context import claim_order_key, select_claims
from cognitive_mesh.traces import TraceRecorder, TraceRecordError, TraceStore
from experience_support import Step, deps_step, failed_step, make_db, model_step
from test_recall import Scripted
from test_retrieval_context import APP, FP1, A, current_graph
from test_trace_context import read, record_with, sql

CONFLICT = ("conflicting", "contested")


def item(n: int, status: str = "supported", sim: float = 0.5, corr: str = "single") -> ClaimItem:
    return ClaimItem(
        claim_id=f"claim:{n:032x}",
        name=f"claim {n}",
        predicate="depends_on",
        subject="module:a.py",
        object=f"module:o{n}.py",
        detail="imports",
        status=status,
        meaning="m",
        corroboration=corr,
        best_similarity=sim,
    )


def test_the_written_order_is_status_then_corroboration_then_similarity_then_id() -> None:
    wanted = [
        item(1, "conflicting"),
        item(2, "contested"),
        item(3, "stale"),
        item(4, "expired"),
        item(5, "unknown"),
        item(6, "validated", corr="corroborated"),
        item(7, "validated", corr="single", sim=0.9),
        item(8, "validated", corr="single", sim=0.4),
        item(9, "validated", corr="single", sim=0.4),
        item(10, "supported"),
        item(11, "unsupported"),
    ]
    shuffled = wanted[::2][::-1] + wanted[1::2]
    kept, dropped, _, _ = select_claims(shuffled)
    assert [c.claim_id for c in kept] == [c.claim_id for c in wanted] and dropped == []
    assert [c.status for c in kept[:2]] == ["conflicting", "contested"] or {
        c.status for c in kept[:2]
    } == set(CONFLICT)
    assert sorted(wanted, key=claim_order_key) == wanted


def test_selection_is_the_same_every_time() -> None:
    claims = [item(i, "supported", sim=(i % 7) / 10) for i in range(80)]
    first = select_claims(claims)
    assert select_claims(claims[::-1]) == first
    assert select_claims(claims[::3] + claims[1::3] + claims[2::3]) == first


def test_a_conflict_ranked_last_is_never_cut_for_size() -> None:
    claims = [item(i, "supported", sim=0.9) for i in range(MAX_CLAIMS + 10)]
    claims.append(item(999, "conflicting", sim=0.0))
    kept, dropped, conflicts, over = select_claims(claims)
    assert any(c.claim_id == item(999).claim_id for c in kept)
    assert len(kept) == MAX_CLAIMS and len(dropped) == 11 and conflicts == 1 and not over
    assert all(c.status == "supported" for c in dropped)


def test_conflicts_alone_over_the_limit_are_all_kept_and_flagged() -> None:
    claims = [item(i, "conflicting") for i in range(MAX_CLAIMS + 5)] + [
        item(900 + i) for i in range(4)
    ]
    kept, dropped, conflicts, over = select_claims(claims)
    assert len(kept) == MAX_CLAIMS + 5 and len(dropped) == 4
    assert conflicts == MAX_CLAIMS + 5 and over is True


def test_a_prompt_cut_for_size_removes_the_conflicts_last() -> None:
    big = "x" * 3000
    claims = [item(i, "supported").model_copy(update={"detail": big}) for i in range(20)]
    claims.append(item(500, "conflicting").model_copy(update={"detail": big}))
    kept, _, _, _ = select_claims(claims)
    pack = RecalledContext(claims=tuple(kept), empty=False)
    data = prior_context_for_prompt(pack)
    assert len(json.dumps(data)) <= MAX_PRIOR_CONTEXT_CHARS
    claims_json = data["claims"]
    assert isinstance(claims_json, list)
    names = [c["claim_id"] for c in claims_json if isinstance(c, dict)]
    omitted = data["omitted_for_size"]
    assert item(500).claim_id in names and isinstance(omitted, int) and omitted > 0


def gated(
    tmp_path: Path,
    runs: Mapping[str, tuple[list[Step], str | None]],
    searcher: Scripted,
    profile: ContextProfile,
    roots: Mapping[str, str] | None = None,
    apps: Mapping[str, str] | None = None,
) -> GatedContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, runs, roots, apps)
    with TraceStore.open_existing(db) as store:
        snap: Snapshot = build_from_traces(store)
        result = recall("q", searcher, store, snap, k=5, min_similarity=0.2)
        return build_context(
            "q",
            result,
            ExperienceGraph(snap),
            current_graph(),
            store,
            APP,
            profile=profile,
        )


RUNS = {"r1": ([model_step("r1", "s1"), failed_step("r1", "s2")], FP1)}
HIT = Scripted([("r1", "s2", 1, 0.9)], basis=None, status="failure")


def test_recall_profile_offers_raw_traces_of_runs_with_no_recorded_application(
    tmp_path: Path,
) -> None:
    roots = {"r1": "/work/another_app"}
    strict = gated(tmp_path / "a", RUNS, HIT, "precision", roots=roots).pack
    wide = gated(tmp_path / "b", RUNS, HIT, "recall", roots=roots).pack
    assert strict.raw == () and strict.withheld.not_applicable == 1
    (raw,) = wide.raw
    assert raw.identity == "unrecorded" and wide.profile == "recall"
    assert strict.profile == "precision"


def test_recall_profile_still_withholds_a_run_of_a_recorded_other_application(
    tmp_path: Path,
) -> None:
    got = gated(
        tmp_path,
        RUNS,
        HIT,
        "recall",
        roots={"r1": "/work/another_app"},
        apps={"r1": "other-app"},
    ).pack
    assert got.raw == () and got.withheld.not_applicable == 1


def test_recall_profile_does_not_bypass_the_subject_gate(tmp_path: Path) -> None:
    gone = {"r1": ([deps_step("r1", "s1", "module:src/elsewhere/gone.py", ["module:x"])], FP1)}
    got = gated(tmp_path, gone, Scripted([("r1", "s1", 1, 0.99)]), "recall").pack
    assert got.claims == () and got.empty and got.withheld.not_applicable == 1


def test_the_pack_lists_what_it_dropped_by_id(tmp_path: Path) -> None:
    objects = [f"module:src/dep_app/o{i}.py" for i in range(MAX_CLAIMS + 3)]
    runs = {"r1": ([deps_step("r1", "s1", A, objects)], FP1)}
    got = gated(tmp_path, runs, Scripted([("r1", "s1", 1, 0.9)]), "precision").pack
    assert got.withheld.over_limit == 3 and len(got.withheld.dropped) == 3
    assert {d.kind for d in got.withheld.dropped} == {"claim"}
    kept = {c.claim_id for c in got.claims}
    assert not kept & {d.item_id for d in got.withheld.dropped}
    assert got.conflicts_kept == 0 and got.conflicts_over_limit is False


def test_the_profile_is_stored_with_the_run_and_read_back_later(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    pack = RecalledContext(
        claims=(item(1, "conflicting"),),
        empty=False,
        profile="recall",
        conflicts_kept=1,
    )
    record_with(db, pack)
    back = read(db).recalled
    assert back is not None and back.profile == "recall" and back.conflicts_kept == 1
    assert sql_rows(db, "SELECT profile FROM run_context") == [("recall",)]


def sql_rows(db: Path, statement: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


def test_a_profile_column_that_disagrees_with_the_pack_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, RecalledContext(claims=(item(1),), empty=False, profile="recall"))
    sql(db, "UPDATE run_context SET profile = 'precision'")
    with pytest.raises(TraceRecordError, match="profile"):
        read(db)


def test_an_older_run_without_a_stored_profile_reads_as_precision(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    record_with(db, RecalledContext(claims=(item(1),), empty=False))
    sql(db, "UPDATE run_context SET profile = NULL")
    back = read(db).recalled
    assert back is not None and back.profile == "precision"


def test_the_recorder_still_stores_a_pack_without_a_profile_argument(tmp_path: Path) -> None:
    assert TraceRecorder is not None
    record_with(tmp_path / "t.sqlite3", RecalledContext.none())
    assert sql_rows(tmp_path / "t.sqlite3", "SELECT profile FROM run_context") == [("precision",)]
