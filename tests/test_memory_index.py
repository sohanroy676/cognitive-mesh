"""Memory index tests (ADR-0014): ranking, traceability, duplicates, incompatible models."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.memory import MemoryIndex, MemoryIndexError, SourceCheck
from cognitive_mesh.traces import TraceStore
from memory_support import FakeEmbedder, add_run, build_fixture_db, success


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.sqlite3"
    build_fixture_db(path)
    return path


def build(db: Path, index_dir: Path, embedder: FakeEmbedder | None = None):  # type: ignore[no-untyped-def]
    emb = embedder or FakeEmbedder()
    with TraceStore.open_existing(db) as store:
        return MemoryIndex.open_for_write(index_dir, emb).index_traces(store)


def search(db: Path, index_dir: Path, query: str, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("k", 5)
    kw.setdefault("min_similarity", 0.0)
    with TraceStore.open_existing(db) as store:
        return MemoryIndex.open_existing(index_dir, FakeEmbedder()).search(query, store, **kw)


def test_relevant_experiences_rank_above_unrelated_ones(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    found = search(db, tmp_path / "idx", "invoice totals and sales tax")
    assert found.hits[0].run_id == "run-invoice"
    assert [h.run_id for h in found.hits][0] != "run-deps"
    deps = search(db, tmp_path / "idx", "dependencies of module a")
    assert deps.hits[0].run_id == "run-deps"
    down = search(db, tmp_path / "idx", "refund model provider unavailable")
    assert down.hits[0].run_id == "run-down"
    assert down.hits[0].result_status == "failure"


def test_hits_point_to_their_trace_rows_and_carry_basis_and_the_disclaimer(
    db: Path, tmp_path: Path
) -> None:
    build(db, tmp_path / "idx")
    found = search(db, tmp_path / "idx", "invoice totals")
    top = found.hits[0]
    assert (top.run_id, top.step_id, top.attempt_no) == ("run-invoice", "explain", 1)
    assert top.basis == "model_unverified"
    assert "not evidence that a past result was correct" in found.note
    with TraceStore.open_existing(db) as store:
        assert store.get(top.run_id).steps[0].step_id == top.step_id


def test_indexing_twice_does_not_duplicate_or_re_embed(db: Path, tmp_path: Path) -> None:
    emb = FakeEmbedder()
    first = build(db, tmp_path / "idx", emb)
    assert (first.added, first.updated, first.unchanged) == (3, 0, 0)
    emb.calls.clear()
    second = build(db, tmp_path / "idx", emb)
    assert (second.added, second.updated, second.unchanged) == (0, 0, 3)
    assert emb.calls == []
    assert MemoryIndex.open_existing(tmp_path / "idx", emb).count() == 3


def test_a_new_run_is_added_and_old_ones_stay_unchanged(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    add_run(db, "run-new", [("s", "c", {}, success("run-new", "s", "c", {"a": 1}))])
    report = build(db, tmp_path / "idx")
    assert (report.added, report.updated, report.unchanged) == (1, 0, 3)


def test_an_empty_trace_database_gives_an_empty_index_and_an_explicit_empty_search(
    tmp_path: Path,
) -> None:
    db = tmp_path / "empty.sqlite3"
    TraceStore.create(db).close()
    report = build(db, tmp_path / "idx")
    assert (report.added, report.unchanged) == (0, 0)
    found = search(db, tmp_path / "idx", "anything")
    assert found.hits == ()
    assert "index is empty" in found.message


def test_a_query_nothing_matches_gives_an_explicit_no_result(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    found = search(db, tmp_path / "idx", "zebra quantum xylophone", min_similarity=0.5)
    assert found.hits == ()
    assert found.dropped_weak >= 1
    assert found.message == "no sufficiently similar experience"


def test_a_deleted_source_row_is_reported_and_never_returned_as_a_hit(
    db: Path, tmp_path: Path
) -> None:
    build(db, tmp_path / "idx")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM attempts WHERE run_id = 'run-invoice'")
    conn.execute("DELETE FROM steps WHERE run_id = 'run-invoice'")
    conn.execute("DELETE FROM runs WHERE run_id = 'run-invoice'")
    conn.commit()
    conn.close()
    found = search(db, tmp_path / "idx", "invoice totals and sales tax")
    assert all(h.run_id != "run-invoice" for h in found.hits)
    missing = [u for u in found.unverified if u.run_id == "run-invoice"]
    assert missing and missing[0].reason is SourceCheck.MISSING


def test_a_changed_source_row_is_reported_as_changed(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT plan_json FROM runs WHERE run_id = 'run-invoice'").fetchone()
    conn.execute(
        "UPDATE runs SET plan_json = ? WHERE run_id = 'run-invoice'",
        (row[0].replace("billing/invoice.py", "billing/other.py"),),
    )
    conn.commit()
    conn.close()
    found = search(db, tmp_path / "idx", "invoice totals and sales tax")
    assert all(h.run_id != "run-invoice" for h in found.hits)
    changed = [u for u in found.unverified if u.run_id == "run-invoice"]
    assert changed and changed[0].reason is SourceCheck.CHANGED
    assert found.message in ("", "no similar experience with a confirmed source")


def test_changed_source_text_is_re_embedded_on_the_next_index(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT plan_json FROM runs WHERE run_id = 'run-invoice'").fetchone()
    conn.execute(
        "UPDATE runs SET plan_json = ? WHERE run_id = 'run-invoice'",
        (row[0].replace("billing/invoice.py", "billing/other.py"),),
    )
    conn.commit()
    conn.close()
    report = build(db, tmp_path / "idx")
    assert (report.updated, report.unchanged) == (1, 2)
    found = search(db, tmp_path / "idx", "invoice totals and sales tax")
    assert found.hits[0].run_id == "run-invoice"


def test_a_run_that_became_malformed_is_unreadable_not_trusted(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = 'run-deps'")
    conn.commit()
    conn.close()
    found = search(db, tmp_path / "idx", "dependencies of module a")
    assert all(h.run_id != "run-deps" for h in found.hits)
    assert any(
        u.run_id == "run-deps" and u.reason is SourceCheck.UNREADABLE for u in found.unverified
    )
    report = build(db, tmp_path / "idx")
    assert report.malformed_runs == ("run-deps",)


def test_a_vanished_source_is_counted_as_orphaned_on_reindex(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in ("attempts", "steps", "runs"):
        conn.execute(f"DELETE FROM {table} WHERE run_id = 'run-down'")  # noqa: S608
    conn.commit()
    conn.close()
    assert build(db, tmp_path / "idx").orphaned == 1


def test_an_index_from_another_model_is_refused_not_mixed(db: Path, tmp_path: Path) -> None:
    build(db, tmp_path / "idx")
    with pytest.raises(MemoryIndexError, match="different embedding model"):
        MemoryIndex.open_for_write(tmp_path / "idx", FakeEmbedder(name="other"))
    with pytest.raises(MemoryIndexError, match="different embedding model"):
        MemoryIndex.open_existing(tmp_path / "idx", FakeEmbedder(name="other"))


def test_an_embedder_returning_the_wrong_dimension_is_refused(db: Path, tmp_path: Path) -> None:
    class Wrong(FakeEmbedder):
        def embed(self, texts):  # type: ignore[no-untyped-def]
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(MemoryIndexError, match="wrong size"), TraceStore.open_existing(db) as store:
        MemoryIndex.open_for_write(tmp_path / "idx", Wrong()).index_traces(store)
    assert MemoryIndex.open_existing(tmp_path / "idx", FakeEmbedder()).count() == 0


def test_readers_never_create_an_index(db: Path, tmp_path: Path) -> None:
    with pytest.raises(MemoryIndexError, match="does not exist"):
        MemoryIndex.open_existing(tmp_path / "nope", FakeEmbedder())
    assert not (tmp_path / "nope").exists()
    (tmp_path / "plain").mkdir()
    with pytest.raises(MemoryIndexError, match="does not exist"):
        MemoryIndex.open_existing(tmp_path / "plain", FakeEmbedder())
    assert list((tmp_path / "plain").iterdir()) == []


@pytest.mark.parametrize(
    ("query", "k", "minimum"),
    [
        ("", 5, 0.1),
        ("   ", 5, 0.1),
        ("q", 0, 0.1),
        ("q", 51, 0.1),
        ("q", 5, 1.5),
        ("q" * 2001, 5, 0.1),
    ],
)
def test_bad_search_arguments_are_rejected(
    db: Path, tmp_path: Path, query: str, k: int, minimum: float
) -> None:
    build(db, tmp_path / "idx")
    with pytest.raises(ValueError):  # noqa: PT011
        search(db, tmp_path / "idx", query, k=k, min_similarity=minimum)


def test_the_index_survives_a_new_process(db: Path, tmp_path: Path) -> None:
    import subprocess
    import sys
    import textwrap

    build(db, tmp_path / "idx")
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(Path(__file__).parent)!r})
        from memory_support import FakeEmbedder
        from cognitive_mesh.memory import MemoryIndex
        from cognitive_mesh.traces import TraceStore
        with TraceStore.open_existing(Path({str(db)!r})) as s:
            r = MemoryIndex.open_existing(Path({str(tmp_path / "idx")!r}), FakeEmbedder()).search(
                "invoice totals", s, k=3, min_similarity=0.0)
        print(r.hits[0].run_id)
        """
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)  # noqa: S603
    assert out.stdout.strip() == "run-invoice", out.stderr[-1500:]


def test_errors_never_contain_the_query_or_document_text(db: Path, tmp_path: Path) -> None:
    canary = "CANARY-QUERY-TEXT"
    build(db, tmp_path / "idx")

    class Boom(FakeEmbedder):
        def embed(self, texts):  # type: ignore[no-untyped-def]
            raise MemoryIndexError("the embedding model failed to embed the text")

    with TraceStore.open_existing(db) as store:
        index = MemoryIndex.open_existing(tmp_path / "idx", FakeEmbedder())
        index._embedder = Boom()
        with pytest.raises(MemoryIndexError) as err:
            index.search(canary, store, k=3, min_similarity=0.0)
    assert canary not in str(err.value)
