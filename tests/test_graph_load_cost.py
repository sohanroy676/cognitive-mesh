"""ADR-0027: the graph file hashes the stored texts, and loading does no repeated work."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    DiagnosticCode,
    ExpEdge,
    ExperienceDiagnostic,
    ExperienceGraphError,
    Snapshot,
    TraceRef,
    build_from_traces,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.models import (
    ContentHasher,
    EdgeKind,
    ExpNode,
    NodeKind,
    content_sha256,
    ref_key,
)
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, make_db, unresolved_step

A, B, C = "module:a.py", "module:b.py", "module:c.py"

# Format 6 of this fixture. If this changes, every stored graph file changes meaning.
GOLDEN_HASH = "ae93a1a4fe75bdea7705e2574811b81c67b193b37404b6271321292acbacd4d5"
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
        snap = build_from_traces(store)
    note = ExperienceDiagnostic(code=DiagnosticCode.RUN_NOT_FINISHED, message="fixed text")
    return Snapshot(nodes=snap.nodes, edges=snap.edges, diagnostics=(*snap.diagnostics, note))


def _saved(tmp_path: Path) -> tuple[Path, Snapshot]:
    tmp_path.mkdir(exist_ok=True)
    snap = _snapshot(tmp_path)
    path = tmp_path / "g.sqlite3"
    graph_store.save(path, snap)
    return path, snap


def _sql(path: Path, statement: str, *args: object) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(statement, args)
        conn.commit()
    finally:
        conn.close()


def test_the_content_hash_is_pinned(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    assert (len(snap.nodes), len(snap.edges)) == GOLDEN_COUNTS
    assert content_sha256(snap.nodes, snap.edges, snap.diagnostics) == GOLDEN_HASH


def test_save_load_and_the_in_memory_hash_agree(tmp_path: Path) -> None:
    path, snap = _saved(tmp_path)
    loaded = graph_store.load(path)
    assert loaded.snapshot == snap
    assert loaded.content_sha256 == GOLDEN_HASH
    assert loaded.content_sha256 == content_sha256(snap.nodes, snap.edges, snap.diagnostics)


def test_the_hash_does_not_depend_on_the_order_things_arrive_in(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    pool = tuple(sorted({r for e in snap.edges for r in e.refs}, key=ref_key))[:3]
    many = tuple(e.model_copy(update={"refs": pool}) for e in snap.edges)
    flipped = tuple(e.model_copy(update={"refs": tuple(reversed(pool))}) for e in reversed(many))
    one = content_sha256(snap.nodes, many, snap.diagnostics)
    two = content_sha256(tuple(reversed(snap.nodes)), flipped, snap.diagnostics)
    assert one == two


def test_refs_of_an_edge_come_back_in_ref_key_order(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    pool = sorted({r for e in snap.edges for r in e.refs}, key=ref_key, reverse=True)
    # Real builds give every edge one reference; give four edges several, newest key first,
    # the same references on all of them.
    edges = tuple(
        e.model_copy(update={"refs": tuple(pool[:4])}) if i % 10 == 0 else e
        for i, e in enumerate(snap.edges)
    )
    multi = Snapshot(nodes=snap.nodes, edges=edges, diagnostics=snap.diagnostics)
    path = tmp_path / "multi.sqlite3"
    digest = graph_store.save(path, multi)
    loaded = graph_store.load(path)
    shared = [e for e in loaded.snapshot.edges if len(e.refs) > 1]
    assert len(shared) == 4
    for edge in shared:
        keys = [ref_key(r) for r in edge.refs]
        assert keys == sorted(keys)
    back = loaded.snapshot
    again = content_sha256(back.nodes, back.edges, back.diagnostics)
    assert loaded.content_sha256 == digest == again


@pytest.mark.parametrize(
    ("table", "column"),
    [("nodes", "node_json"), ("diagnostics", "diagnostic_json"), ("edge_refs", "ref_json")],
)
def test_a_whitespace_only_edit_of_any_stored_record_is_refused(
    tmp_path: Path, table: str, column: str
) -> None:
    path, _ = _saved(tmp_path)
    # `{"a":1}` -> `{ "a":1}` parses to the same record but is different stored text.
    _sql(path, f"UPDATE {table} SET {column} = replace({column}, '{{\"', '{{ \"')")  # noqa: S608
    with pytest.raises(ExperienceGraphError, match="hash does not match"):
        graph_store.load(path)


def test_an_edited_edge_row_is_refused(tmp_path: Path) -> None:
    path, _ = _saved(tmp_path)
    _sql(
        path,
        "UPDATE edges SET target = 'run:r2' "
        "WHERE rowid = (SELECT rowid FROM edges WHERE kind = 'about' LIMIT 1)",
    )
    with pytest.raises(ExperienceGraphError):
        graph_store.load(path)


@pytest.mark.parametrize("source", ["run:r1", "zzz-sorts-after-every-edge"])
def test_a_reference_that_belongs_to_no_edge_is_refused(tmp_path: Path, source: str) -> None:
    # The first sorts among the edges and stalls the pairing; the second sorts after the last
    # edge, where it would be neither parsed nor hashed unless the loader checks for leftovers.
    path, _ = _saved(tmp_path)
    ref = ref_key(TraceRef(run_id="r1"))
    _sql(path, "INSERT INTO edge_refs VALUES (?, 'nowhere', 'has_step', ?)", source, ref)
    with pytest.raises(ExperienceGraphError, match="malformed record"):
        graph_store.load(path)


def test_a_malformed_record_is_still_malformed_not_a_hash_error(tmp_path: Path) -> None:
    path, _ = _saved(tmp_path)
    _sql(path, "UPDATE nodes SET node_json = replace(node_json, '{', '{\"extra\":1,')")
    with pytest.raises(ExperienceGraphError, match="malformed record"):
        graph_store.load(path)


def test_stored_text_that_is_not_ascii_is_a_hash_error_not_a_crash(tmp_path: Path) -> None:
    path, _ = _saved(tmp_path)
    _sql(path, "UPDATE nodes SET node_json = replace(node_json, 'a.py', 'ä.py')")
    with pytest.raises(ExperienceGraphError, match="hash does not match"):
        graph_store.load(path)


def test_a_format_5_file_asks_for_a_rebuild(tmp_path: Path) -> None:
    path, _ = _saved(tmp_path)
    _sql(path, "UPDATE meta SET value = '5' WHERE key = 'format_version'")
    with pytest.raises(ExperienceGraphError, match="experience build"):
        graph_store.load(path)


def test_ids_order_the_same_in_sqlite_and_python(tmp_path: Path) -> None:
    # Code-point order: a < z < é < fullwidth z (BMP) < an emoji (astral). UTF-16 would put the
    # emoji before the fullwidth z; SQLite compares UTF-8 bytes, which agree with code points.
    names = ["a", "z", "é", "ｚ", "\U0001f600"]
    ref = TraceRef(run_id="r")
    nodes = tuple(ExpNode(id=f"t:{n}", kind=NodeKind.TARGET, name=n) for n in names)
    edges = tuple(
        ExpEdge(
            source=f"t:{names[i]}",
            target=f"t:{names[(i + 1) % 5]}",
            kind=EdgeKind.ABOUT,
            refs=(ref,),
        )
        for i in range(5)
    )
    path = tmp_path / "unicode.sqlite3"
    digest = graph_store.save(path, Snapshot(nodes=nodes, edges=edges))
    loaded = graph_store.load(path)
    assert loaded.content_sha256 == digest
    assert [n.name for n in loaded.snapshot.nodes] == names


def test_framing_keeps_neighbouring_records_apart() -> None:
    one, two = ContentHasher(), ContentHasher()
    one.node("ab")
    one.node("c")
    two.node("a")
    two.node("bc")
    assert one.hexdigest() != two.hexdigest()
