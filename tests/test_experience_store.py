"""Saving and reloading the experience graph file (ADR-0015)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    EdgeKind,
    ExpEdge,
    ExperienceGraphError,
    Snapshot,
    TraceRef,
    build_from_traces,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db

A, B = "module:a.py", "module:b.py"
STAMP = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def snapshot_of(db: Path) -> Snapshot:
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


@pytest.fixture
def saved(tmp_path: Path) -> tuple[Path, Snapshot]:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B])], "fp1"),
            "r2": ([deps_step("r2", "s1", A, [B])], "fp2"),
        },
    )
    snap = snapshot_of(db)
    graph = tmp_path / "graph.sqlite3"
    graph_store.save(graph, snap, built_at=STAMP)
    return graph, snap


def sql(path: Path, statement: str, *args: object) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(statement, args)
        conn.commit()
    finally:
        conn.close()


def count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608
    finally:
        conn.close()


def test_a_saved_graph_reloads_identically(saved: tuple[Path, Snapshot]) -> None:
    graph, snap = saved
    loaded = graph_store.load(graph)
    assert loaded.snapshot == snap
    assert loaded.built_at == STAMP
    assert loaded.content_sha256 == graph_store.save(graph, snap, built_at=STAMP)


def test_saving_twice_changes_nothing_and_leaves_no_duplicate_rows(
    saved: tuple[Path, Snapshot],
) -> None:
    graph, snap = saved
    before = {t: count(graph, t) for t in ("nodes", "edges", "edge_refs", "diagnostics")}
    graph_store.save(graph, snap, built_at=STAMP)
    after = {t: count(graph, t) for t in before}
    assert before == after
    assert graph_store.load(graph).snapshot == snap


def test_a_rebuild_replaces_old_content(saved: tuple[Path, Snapshot], tmp_path: Path) -> None:
    graph, _ = saved
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    small = snapshot_of(make_db(other_dir, {"only": ([deps_step("only", "s", B, [A])], None)}))
    graph_store.save(graph, small, built_at=STAMP)
    loaded = graph_store.load(graph).snapshot
    assert loaded == small
    assert not any("r1" in n.id for n in loaded.nodes)


def test_missing_file_is_refused_and_nothing_is_created(tmp_path: Path) -> None:
    target = tmp_path / "nope.sqlite3"
    with pytest.raises(ExperienceGraphError, match="does not exist"):
        graph_store.load(target)
    assert not target.exists()


def test_a_trace_database_is_not_a_graph_file(tmp_path: Path) -> None:
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], None)})
    with pytest.raises(ExperienceGraphError, match="not an experience graph"):
        graph_store.load(db)
    before = db.read_bytes()
    with pytest.raises(ExperienceGraphError, match="not an experience graph"):
        graph_store.save(db, snapshot_of(db))
    assert db.read_bytes() == before  # the trace database is untouched


def test_garbage_file_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"this is not a database" * 50)
    with pytest.raises(ExperienceGraphError):
        graph_store.load(junk)


def test_newer_schema_version_is_refused(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(graph, "PRAGMA user_version = 2")
    with pytest.raises(ExperienceGraphError, match="newer than this program"):
        graph_store.load(graph)
    with pytest.raises(ExperienceGraphError, match="newer than this program"):
        graph_store.save(graph, Snapshot(nodes=(), edges=()))


def test_edited_node_record_fails_the_hash(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(
        graph,
        "UPDATE nodes SET node_json = replace(node_json, 'a.py depends_on', 'z.py depends_on')",
    )
    with pytest.raises(ExperienceGraphError, match="hash does not match"):
        graph_store.load(graph)


def test_edited_stored_hash_fails(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(graph, "UPDATE meta SET value = ? WHERE key = 'content_sha256'", "0" * 64)
    with pytest.raises(ExperienceGraphError, match="hash does not match"):
        graph_store.load(graph)


def test_deleted_node_leaves_a_dangling_edge(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(graph, "DELETE FROM nodes WHERE id = 'run:r1'")
    with pytest.raises(ExperienceGraphError, match="endpoint is missing"):
        graph_store.load(graph)


def test_injected_edge_to_a_missing_node_is_refused(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    ref = TraceRef(run_id="r1").model_dump_json()
    sql(graph, "INSERT INTO edges VALUES ('run:r1', 'ghost', 'has_step')")
    sql(graph, "INSERT INTO edge_refs VALUES ('run:r1', 'ghost', 'has_step', ?)", ref)
    with pytest.raises(ExperienceGraphError, match="endpoint is missing"):
        graph_store.load(graph)


def test_the_schema_itself_refuses_a_duplicate_edge(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    with pytest.raises(sqlite3.IntegrityError):
        sql(graph, "INSERT INTO edges SELECT * FROM edges LIMIT 1")


def test_an_edge_without_references_is_malformed(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(graph, "DELETE FROM edge_refs WHERE kind = 'has_step'")
    with pytest.raises(ExperienceGraphError, match="malformed record"):
        graph_store.load(graph)


def test_unknown_field_in_a_record_is_malformed(saved: tuple[Path, Snapshot]) -> None:
    graph, _ = saved
    sql(
        graph,
        "UPDATE nodes SET node_json = replace(node_json, '{', '{\"extra\":1,') WHERE id = 'run:r1'",
    )
    with pytest.raises(ExperienceGraphError, match="malformed record"):
        graph_store.load(graph)


def test_save_refuses_an_inconsistent_snapshot_and_keeps_the_old_file(
    saved: tuple[Path, Snapshot],
) -> None:
    graph, snap = saved
    bad = Snapshot(
        nodes=snap.nodes,
        edges=(
            *snap.edges,
            ExpEdge(
                source="run:r1",
                target="ghost",
                kind=EdgeKind.HAS_STEP,
                refs=(TraceRef(run_id="r1"),),
            ),
        ),
    )
    with pytest.raises(ExperienceGraphError, match="endpoint is missing"):
        graph_store.save(graph, bad)
    assert graph_store.load(graph).snapshot == snap


def test_save_refuses_duplicate_nodes(saved: tuple[Path, Snapshot]) -> None:
    graph, snap = saved
    with pytest.raises(ExperienceGraphError, match="duplicate node"):
        graph_store.save(graph, Snapshot(nodes=(*snap.nodes, snap.nodes[0]), edges=snap.edges))
