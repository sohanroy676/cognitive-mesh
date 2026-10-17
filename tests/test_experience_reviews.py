"""The append-only reviews file (ADR-0016)."""

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.experience.reviews import ReviewStore, ReviewStoreError, Verdict

CLAIM = "claim:" + "ab" * 16
FP = "c" * 64
T1 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def add(store: ReviewStore, verdict: Verdict = Verdict.CONFIRMED, note: str = "looked") -> None:
    store.add(CLAIM, FP, verdict, note, "alice", now=T1)


def test_reviews_persist_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        add(store)
        add(store, Verdict.REJECTED, "no, it is wrong")
    with ReviewStore.open_existing(path) as store:
        found = store.list_reviews(CLAIM)
    assert [r.verdict for r in found] == [Verdict.CONFIRMED, Verdict.REJECTED]
    assert found[0].created_at == T1 and found[0].reviewer == "alice"
    assert found[0].fingerprint == FP


def test_reviews_persist_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        add(store)
    code = (
        "import sys; from pathlib import Path;"
        "from cognitive_mesh.experience.reviews import ReviewStore;"
        "s = ReviewStore.open_existing(Path(sys.argv[1]));"
        "print(len(s.list_reviews()))"
    )
    out = subprocess.run(  # noqa: S603  # fixed args, own interpreter
        [sys.executable, "-I", "-c", code, str(path)], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "1"


def test_reader_never_creates_a_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(ReviewStoreError, match="does not exist"):
        ReviewStore.open_existing(path)
    assert not path.exists()


def test_reader_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    ReviewStore.create(path).close()
    with (
        ReviewStore.open_existing(path) as store,
        pytest.raises(ReviewStoreError, match="read-only"),
    ):
        add(store)


def test_file_is_append_only_even_with_direct_sql(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        add(store)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE reviews SET verdict = 'rejected'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM reviews")
    conn.close()


def test_foreign_and_newer_files_are_refused(tmp_path: Path) -> None:
    foreign = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(foreign)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    with pytest.raises(ReviewStoreError, match="not a reviews file"):
        ReviewStore.create(foreign)
    with pytest.raises(ReviewStoreError, match="not a reviews file"):
        ReviewStore.open_existing(foreign)
    path = tmp_path / "r.sqlite3"
    ReviewStore.create(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(ReviewStoreError, match="newer"):
        ReviewStore.open_existing(path)


def test_not_a_database_is_refused_with_a_fixed_message(tmp_path: Path) -> None:
    path = tmp_path / "junk.sqlite3"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(ReviewStoreError) as err:
        ReviewStore.open_existing(path)
    assert "this is not sqlite" not in str(err.value)


@pytest.mark.parametrize(
    ("claim", "fp", "note", "by"),
    [
        ("claim:nope", FP, "n", "a"),
        (CLAIM, "short", "n", "a"),
        (CLAIM, FP, "", "a"),
        (CLAIM, FP, "   ", "a"),
        (CLAIM, FP, "n", ""),
        (CLAIM, FP, "x" * 1001, "a"),
        (CLAIM, FP, "n", "a" * 101),
        (CLAIM, FP, "line\nbreak", "a"),
        (CLAIM, FP, "esc\x1b[31m", "a"),
        (CLAIM, FP, "n", "bell\x07"),
    ],
)
def test_invalid_reviews_are_refused_and_nothing_is_stored(
    tmp_path: Path, claim: str, fp: str, note: str, by: str
) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        with pytest.raises(ReviewStoreError):
            store.add(claim, fp, Verdict.CONFIRMED, note, by)
        assert store.list_reviews() == []


def test_secrets_in_notes_are_redacted_before_storing(tmp_path: Path) -> None:
    secret = "gsk_" + "a1B2c3D4" * 4  # assembled at runtime
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        review = store.add(CLAIM, FP, Verdict.CONFIRMED, f"key {secret} here", "alice")
    assert secret not in review.note
    assert secret.encode() not in path.read_bytes()


def test_malformed_row_is_an_error_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        add(store)
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER reviews_no_update")
    conn.execute("UPDATE reviews SET created_at = 'not a time'")
    conn.commit()
    conn.close()
    with (
        ReviewStore.open_existing(path) as store,
        pytest.raises(ReviewStoreError, match="malformed"),
    ):
        store.list_reviews()


def test_only_the_asked_claim_is_listed(tmp_path: Path) -> None:
    other = "claim:" + "cd" * 16
    with ReviewStore.create(tmp_path / "r.sqlite3") as store:
        add(store)
        store.add(other, FP, Verdict.REJECTED, "n", "bob")
        assert [r.claim_id for r in store.list_reviews(other)] == [other]
        assert len(store.list_reviews()) == 2
