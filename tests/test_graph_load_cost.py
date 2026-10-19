"""ADR-0027 level A: loading and hashing do less repeated work and change nothing stored."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    ExpEdge,
    ExperienceGraphError,
    Snapshot,
    TraceRef,
    build_from_traces,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.models import canonical, content_sha256, ref_key
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db, unresolved_step

A, B, C = "module:a.py", "module:b.py", "module:c.py"

# Taken from the code at e19c0c6, before ADR-0027. If these change, stored graph files change.
GOLDEN_HASH = "f07490c2839ad670b5a121e9a94b067f12f2e6e070f119527626d22c0e10373b"
GOLDEN_COUNTS = (24, 38)


def _snapshot(tmp_path: Path) -> Snapshot:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B]), unresolved_step("r1", "u", [C])], "fp1"),
            "r2": (
                [deps_step("r2", "s1", A, [C]), deps_step("r2", "s2", B, [C], truncated=True)],
                "fp2",
            ),
            "r3": ([deps_step("r3", "s1", A, [B])], "fp2"),
        },
        roots={"r1": "app", "r2": "app"},
        apps={"r1": "shop"},
    )
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def test_the_content_hash_is_the_one_recorded_before_the_change(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    assert (len(snap.nodes), len(snap.edges)) == GOLDEN_COUNTS
    assert content_sha256(snap.nodes, snap.edges, snap.diagnostics) == GOLDEN_HASH


def test_a_saved_and_reloaded_graph_has_the_same_hash_and_records(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    path = tmp_path / "g.sqlite3"
    assert graph_store.save(path, snap) == GOLDEN_HASH
    loaded = graph_store.load(path)
    assert loaded.content_sha256 == GOLDEN_HASH
    assert loaded.snapshot == snap


def test_refs_are_ordered_by_ref_key_after_a_load(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    pool = sorted({r for e in snap.edges for r in e.refs}, key=ref_key, reverse=True)
    # Real builds give every edge one reference; give three edges several, newest key first,
    # the same references on all of them.
    edges = tuple(
        e.model_copy(update={"refs": tuple(pool[:4])}) if i % 10 == 0 else e
        for i, e in enumerate(snap.edges)
    )
    multi = Snapshot(nodes=snap.nodes, edges=edges, diagnostics=snap.diagnostics)
    path = tmp_path / "g.sqlite3"
    digest = graph_store.save(path, multi)
    loaded = graph_store.load(path)
    shared = [e for e in loaded.snapshot.edges if len(e.refs) > 1]
    assert len(shared) == 4
    for edge in shared:
        keys = [ref_key(r) for r in edge.refs]
        assert len(keys) == 4 and keys == sorted(keys)
    back = loaded.snapshot
    assert (
        loaded.content_sha256 == digest == content_sha256(back.nodes, back.edges, back.diagnostics)
    )


def test_equal_refs_that_are_distinct_objects_hash_like_shared_ones() -> None:
    def ref() -> TraceRef:
        return TraceRef(run_id="r", step_id="s", attempt_no=1, text_sha256="t" * 8)

    other = TraceRef(run_id="r", step_id="s", attempt_no=2, text_sha256="u" * 8)
    shared = ref()
    one = (
        ExpEdge(source="a", target="b", kind="about", refs=(shared, other)),
        ExpEdge(source="a", target="c", kind="about", refs=(shared, other)),
    )
    two = (
        ExpEdge(source="a", target="b", kind="about", refs=(ref(), other)),
        ExpEdge(source="a", target="c", kind="about", refs=(ref(), other)),
    )
    assert canonical((), one) == canonical((), two)


def test_canonical_text_orders_an_edges_refs_by_their_key() -> None:
    low = TraceRef(run_id="a")
    high = TraceRef(run_id="b")
    edge = ExpEdge(source="x", target="y", kind="about", refs=(high, low))
    refs = json.loads(canonical((), (edge,)))["edges"][0]["refs"]
    assert [r["run_id"] for r in refs] == ["a", "b"]


def test_a_changed_ref_in_a_stored_edge_is_still_refused(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    path = tmp_path / "g.sqlite3"
    graph_store.save(path, snap)
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT rowid, ref_json FROM edge_refs LIMIT 1").fetchone()
        conn.execute(
            "UPDATE edge_refs SET ref_json = ? WHERE rowid = ?",
            (row[1].replace('"run_id":"r', '"run_id":"q'), row[0]),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ExperienceGraphError, match="content hash|malformed"):
        graph_store.load(path)
