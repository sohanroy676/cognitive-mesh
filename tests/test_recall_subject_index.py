"""Subject-index recall (ADR-0019) must equal whole-graph recall claim for claim."""

import time
from pathlib import Path

import pytest

from cognitive_mesh.experience import Snapshot, build_from_traces, check_sources
from cognitive_mesh.experience.assess import assess_claims
from cognitive_mesh.experience.current import Holding
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.experience.models import ExperienceGraphError, ExpNode, NodeKind
from cognitive_mesh.experience.store import check_consistent
from cognitive_mesh.retrieval import RecallResult, recall
from cognitive_mesh.traces import TraceStore
from experience_support import Step, deps_step, make_db, unresolved_step
from test_recall import Scripted, StubCurrent, world

A, B, C, D = "module:a.py", "module:b.py", "module:c.py", "module:d.py"
FP1, FP2 = "1" * 64, "2" * 64


def _whole_graph(db: Path, snap: Snapshot, hits, current=None):  # type: ignore[no-untyped-def]
    """The pre-ADR-0019 algorithm: check and assess everything."""
    with TraceStore.open_existing(db) as store:
        graph = ExperienceGraph(snap)
        return assess_claims(graph, check_sources(snap, store), [], current)


def _same(db: Path, snap: Snapshot, hits, current=None) -> RecallResult:  # type: ignore[no-untyped-def]
    with TraceStore.open_existing(db) as store:
        got = recall("q", Scripted(hits), store, snap, current=current, k=5, min_similarity=0.2)
    full = _whole_graph(db, snap, hits, current)
    assert got.claims, "the scenario must reach at least one claim"
    for claim in got.claims:
        assert claim.assessment == full[claim.assessment.claim_id]
    return got


def test_indexed_recall_equals_whole_graph_on_a_conflict_and_a_stale_claim(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B]), unresolved_step("r1", "u", [D])], FP1),
            "r2": ([deps_step("r2", "s1", A, [C]), deps_step("r2", "s2", B, [C])], FP2),
            "r3": ([deps_step("r3", "s1", A, [B], truncated=True)], FP2),
        },
    )
    hits = [("r1", "s1", 1, 0.9), ("r2", "s1", 1, 0.8), ("r1", "u", 1, 0.5)]
    got = _same(db, snap, hits)
    assert got.subjects_followed == 2
    _same(db, snap, hits, StubCurrent(FP2, Holding.HOLDS))


def test_only_the_followed_subjects_are_assessed(tmp_path: Path) -> None:
    db, snap = world(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], FP1),
            "r2": ([deps_step("r2", "s1", C, [D])], FP1),
        },
    )
    got = _same(db, snap, [("r1", "s1", 1, 0.9)])
    assert got.claims_assessed == 1 and got.subjects_followed == 1


def test_a_missing_source_is_reported_the_same_way(tmp_path: Path) -> None:
    db, snap = world(tmp_path / "a", {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    (tmp_path / "b").mkdir()
    other = make_db(tmp_path / "b", {"zz": ([deps_step("zz", "s1", C, [A])], FP1)})
    with TraceStore.open_existing(other) as store:
        got = recall("q", Scripted([("r1", "s1", 1, 0.9)]), store, snap, k=5, min_similarity=0.2)
    assert got.claims == () and got.unconfirmed[0].reason == "source_missing"


def test_a_hit_with_no_claim_follows_no_subject(tmp_path: Path) -> None:
    db, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    with TraceStore.open_existing(db) as store:
        got = recall("q", Scripted([("nope", "s1", 1, 0.9)]), store, snap, k=5, min_similarity=0.2)
    assert got.subjects_followed == 0 and got.claims_assessed == 0


def test_a_claim_whose_subject_differs_from_its_edge_is_refused(tmp_path: Path) -> None:
    _, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    nodes = tuple(
        n.model_copy(update={"subject": C}) if n.kind is NodeKind.CLAIM else n for n in snap.nodes
    )
    with pytest.raises(ExperienceGraphError, match="subject differs"):
        check_consistent(snap.model_copy(update={"nodes": nodes}))


def test_equal_to_whole_graph_on_5000_runs(tmp_path: Path) -> None:
    mods = [f"module:m{i}.py" for i in range(40)]
    runs: dict[str, tuple[list[Step], str | None]] = {}
    for i in range(5000):
        v = i % 5
        subject, dep = mods[i % 40], mods[(i // 40 + v) % 40]
        steps = [deps_step(f"r{i}", "s", subject, [dep])]
        if i % 7 == 0:
            steps.append(unresolved_step(f"r{i}", "u", [subject]))
        runs[f"r{i}"] = (steps, f"{v}" * 64)
    db = make_db(tmp_path, runs)
    with TraceStore.open_existing(db) as store:
        snap = build_from_traces(store)
    hits = [("r3", "s", 1, 0.9), ("r77", "s", 1, 0.8), ("r140", "u", 1, 0.7)]
    t0 = time.perf_counter()
    with TraceStore.open_existing(db) as store:
        got = recall("q", Scripted(hits), store, snap, k=5, min_similarity=0.2)
    indexed_s = time.perf_counter() - t0
    full = _whole_graph(db, snap, hits)
    assert got.claims
    for claim in got.claims:
        assert claim.assessment == full[claim.assessment.claim_id]
    assert got.claims_assessed < len(ExperienceGraph(snap).claims())
    print(f"indexed recall {indexed_s:.2f}s, claims assessed {got.claims_assessed}")


def test_subject_is_a_claim_field_only(tmp_path: Path) -> None:
    _, snap = world(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)})
    nodes: list[ExpNode] = [n for n in snap.nodes if n.kind is NodeKind.CLAIM]
    assert [n.subject for n in nodes] == [A]
