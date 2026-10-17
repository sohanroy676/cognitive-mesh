"""Building the experience graph from traces (ADR-0015)."""

import sqlite3
from pathlib import Path

from cognitive_mesh.experience import (
    DiagnosticCode,
    EdgeKind,
    NodeKind,
    Snapshot,
    build_from_traces,
    content_sha256,
)
from cognitive_mesh.experience.build import MAX_ID_TEXT
from cognitive_mesh.traces import TraceStore
from experience_support import (
    deps_step,
    failed_step,
    make_db,
    model_step,
    unresolved_step,
)
from memory_support import add_run

A, B, C, D = "module:a.py", "module:b.py", "module:c.py", "module:d.py"


def build(db: Path) -> Snapshot:
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def kinds(snap: Snapshot, kind: NodeKind) -> list[str]:
    return [n.id for n in snap.nodes if n.kind is kind]


def test_direct_dependencies_become_claims_and_transitive_ones_do_not(tmp_path: Path) -> None:
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B, C], [D])], "fp1")})
    snap = build(db)
    names = sorted(n.name for n in snap.nodes if n.kind is NodeKind.CLAIM)
    assert names == [f"{A} depends_on {B}", f"{A} depends_on {C}"]
    assert all(n.validation == "unvalidated" for n in snap.nodes if n.kind is NodeKind.CLAIM)
    assert "target:module:d.py" not in kinds(snap, NodeKind.TARGET)
    assert kinds(snap, NodeKind.RUN) == ["run:r1"]
    assert kinds(snap, NodeKind.STEP) == ["step:r1/s1"]
    assert kinds(snap, NodeKind.ATTEMPT) == ["attempt:r1/s1/1"]


def test_every_edge_has_a_source_reference_and_attempts_carry_the_index_key(tmp_path: Path) -> None:
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], "fp1")})
    snap = build(db)
    assert snap.edges and all(e.refs for e in snap.edges)
    attempt = next(n for n in snap.nodes if n.kind is NodeKind.ATTEMPT)
    assert attempt.ref is not None
    assert attempt.ref.doc_id == "r1/s1/1"
    assert attempt.ref.text_sha256 is not None and len(attempt.ref.text_sha256) == 64
    ids = {n.id for n in snap.nodes}
    assert all(e.source in ids and e.target in ids for e in snap.edges)


def test_same_claim_in_two_runs_is_one_node_with_two_evidence_edges(tmp_path: Path) -> None:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], "fp-old"),
            "r2": ([deps_step("r2", "s1", A, [B])], "fp-new"),
        },
    )
    snap = build(db)
    claims = [n for n in snap.nodes if n.kind is NodeKind.CLAIM]
    assert len(claims) == 1
    evidence = [e for e in snap.edges if e.kind is EdgeKind.EVIDENCED_BY]
    assert sorted(e.target for e in evidence) == ["attempt:r1/s1/1", "attempt:r2/s1/1"]
    fingerprints = {r.source_fingerprint for e in evidence for r in e.refs}
    assert fingerprints == {"fp-old", "fp-new"}  # different code versions stay distinguishable
    assert claims[0].validation == "unvalidated"  # repetition is not validation


def test_model_output_and_failures_never_become_claims(tmp_path: Path) -> None:
    db = make_db(
        tmp_path,
        {"r1": ([model_step("r1", "s1"), failed_step("r1", "s2")], "fp1")},
    )
    snap = build(db)
    assert kinds(snap, NodeKind.CLAIM) == []
    assert sorted(kinds(snap, NodeKind.ATTEMPT)) == ["attempt:r1/s1/1", "attempt:r1/s2/1"]


def test_unresolved_report_claims(tmp_path: Path) -> None:
    db = make_db(tmp_path, {"r1": ([unresolved_step("r1", "s1", ["unresolved:import:x"])], None)})
    snap = build(db)
    claim = next(n for n in snap.nodes if n.kind is NodeKind.CLAIM)
    assert claim.predicate == "is_unresolved"
    assert [e.kind for e in snap.edges if e.source == claim.id].count(EdgeKind.OBJECT) == 0


def test_output_that_does_not_match_the_schema_gives_a_diagnostic_not_claims(
    tmp_path: Path,
) -> None:
    from memory_support import success

    bad = success("r1", "s1", "dependencies_of", {"dependencies": ["module:b.py"]})
    db = make_db(tmp_path, {"r1": ([("s1", "dependencies_of", {"node_id": A}, bad)], None)})
    snap = build(db)
    assert kinds(snap, NodeKind.CLAIM) == []
    assert [d.code for d in snap.diagnostics] == [DiagnosticCode.UNUSABLE_OUTPUT]
    assert kinds(snap, NodeKind.ATTEMPT) == ["attempt:r1/s1/1"]


def test_unfinished_run_is_reported_and_adds_nothing(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(db, "r1", [deps_step("r1", "s1", A, [B])], finish=False)
    snap = build(db)
    assert snap.nodes == ()
    assert [(d.code, d.run_id) for d in snap.diagnostics] == [
        (DiagnosticCode.RUN_NOT_FINISHED, "r1")
    ]


def test_malformed_run_is_reported_and_other_runs_still_build(tmp_path: Path) -> None:
    db = make_db(
        tmp_path,
        {
            "r1": ([failed_step("r1", "s1")], None),
            "r2": ([deps_step("r2", "s1", A, [B])], None),
        },
    )
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET status = 'succeeded' WHERE run_id = 'r1'")
    snap = build(db)
    assert [(d.code, d.run_id) for d in snap.diagnostics] == [(DiagnosticCode.MALFORMED_RUN, "r1")]
    assert kinds(snap, NodeKind.RUN) == ["run:r2"]
    assert all("r1" not in n.id for n in snap.nodes)


def test_build_is_deterministic_and_the_hash_ignores_input_order(tmp_path: Path) -> None:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B, C])], "fp"),
            "r2": ([deps_step("r2", "s1", A, [C, B])], "fp"),
        },
    )
    first, second = build(db), build(db)
    assert first == second
    digest = content_sha256(first.nodes, first.edges)
    assert digest == content_sha256(tuple(reversed(first.nodes)), tuple(reversed(first.edges)))
    changed = first.nodes[:-1]
    assert content_sha256(changed, first.edges) != digest


def test_long_ids_are_clipped_but_stay_distinct(tmp_path: Path) -> None:
    long_a = "module:" + "x" * 400 + "1"
    long_b = "module:" + "x" * 400 + "2"
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", long_a, [long_b])], None)})
    snap = build(db)
    targets = kinds(snap, NodeKind.TARGET)
    assert len(targets) == 2 and len(set(targets)) == 2
    assert all(len(t) <= len("target:") + MAX_ID_TEXT + 13 for t in targets)
    claim = next(n for n in snap.nodes if n.kind is NodeKind.CLAIM)
    assert claim.name == f"{long_a} depends_on {long_b}"  # the full name is kept as data
