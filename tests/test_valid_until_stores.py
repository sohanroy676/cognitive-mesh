"""`valid_until` on reviews and outcomes (ADR-0022): schema 2 and version-1 upgrades."""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.outcomes import APPLICATION_ID as OUTCOMES_ID
from cognitive_mesh.experience.outcomes import (
    OutcomeResult,
    OutcomeSource,
    OutcomeStore,
    OutcomeStoreError,
)
from cognitive_mesh.experience.reviews import APPLICATION_ID as REVIEWS_ID
from cognitive_mesh.experience.reviews import ReviewStore, ReviewStoreError, Verdict

CLAIM = claim_id("depends_on", "module:a.py", "module:b.py", "imports")
FP = "1" * 64
NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
TODAY = NOW.date()


def v1_file(path: Path, table: str) -> None:
    """A version-1 file as released before ADR-0022: no `valid_until` column."""
    reviews = (
        "CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,"
        " fingerprint TEXT NOT NULL, verdict TEXT NOT NULL, note TEXT NOT NULL,"
        " reviewer TEXT NOT NULL, created_at TEXT NOT NULL);"
        " CREATE TRIGGER reviews_no_update BEFORE UPDATE ON reviews"
        " BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;"
        f" PRAGMA application_id = {REVIEWS_ID};"
    )
    outcomes = (
        "CREATE TABLE outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,"
        " fingerprint TEXT NOT NULL, source TEXT NOT NULL, result TEXT NOT NULL,"
        " detail TEXT NOT NULL, recorder TEXT NOT NULL, previous_id INTEGER,"
        " created_at TEXT NOT NULL);"
        " CREATE TRIGGER outcomes_no_update BEFORE UPDATE ON outcomes"
        " BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;"
        f" PRAGMA application_id = {OUTCOMES_ID};"
    )
    conn = sqlite3.connect(path)
    conn.executescript(reviews if table == "reviews" else outcomes)
    conn.execute("PRAGMA user_version = 1")
    if table == "reviews":
        conn.execute(
            "INSERT INTO reviews (claim_id, fingerprint, verdict, note, reviewer, created_at)"
            " VALUES (?, ?, 'confirmed', 'n', 'r', ?)",
            (CLAIM, FP, NOW.isoformat()),
        )
    else:
        conn.execute(
            "INSERT INTO outcomes (claim_id, fingerprint, source, result, detail, recorder,"
            " previous_id, created_at) VALUES (?, ?, 'person', 'succeeded', 'd', 'r', NULL, ?)",
            (CLAIM, FP, NOW.isoformat()),
        )
    conn.commit()
    conn.close()


def version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_a_review_keeps_its_valid_until_across_sessions(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path) as store:
        store.add(CLAIM, FP, Verdict.CONFIRMED, "ok", "me", now=NOW, valid_until=date(2026, 12, 1))
        store.add(CLAIM, FP, Verdict.CONFIRMED, "ok", "me", now=NOW)
    with ReviewStore.open_existing(path) as store:
        got = store.list_reviews()
    assert [r.valid_until for r in got] == [date(2026, 12, 1), None]
    assert version(path) == 2


def test_an_outcome_keeps_its_valid_until_across_sessions(tmp_path: Path) -> None:
    path = tmp_path / "o.sqlite3"
    with OutcomeStore.create(path) as store:
        store.add(
            CLAIM, FP, OutcomeSource.CHECK, OutcomeResult.SUCCEEDED, "d", "me",
            now=NOW, valid_until=TODAY,
        )  # fmt: skip
    with OutcomeStore.open_existing(path) as store:
        (got,) = store.list_outcomes()
    assert got.valid_until == TODAY and version(path) == 2


def test_a_past_valid_until_is_refused_and_stores_nothing(tmp_path: Path) -> None:
    rpath, opath = tmp_path / "r.sqlite3", tmp_path / "o.sqlite3"
    yesterday = date(2026, 10, 17)
    with ReviewStore.create(rpath) as rs, pytest.raises(ReviewStoreError, match="before today"):
        rs.add(CLAIM, FP, Verdict.CONFIRMED, "ok", "me", now=NOW, valid_until=yesterday)
    with OutcomeStore.create(opath) as os_, pytest.raises(OutcomeStoreError, match="before today"):
        os_.add(
            CLAIM, FP, OutcomeSource.PERSON, OutcomeResult.SUCCEEDED, "d", "me",
            now=NOW, valid_until=yesterday,
        )  # fmt: skip
    with ReviewStore.open_existing(rpath) as rs:
        assert rs.list_reviews() == []
    with OutcomeStore.open_existing(opath) as os_:
        assert os_.list_outcomes() == []


def test_a_version_1_reviews_file_reads_without_upgrading_and_never_expires(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r.sqlite3"
    v1_file(path, "reviews")
    before = path.read_bytes()
    with ReviewStore.open_existing(path) as store:
        (got,) = store.list_reviews()
    assert got.valid_until is None
    assert path.read_bytes() == before and version(path) == 1


def test_a_writer_upgrades_a_version_1_reviews_file_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    v1_file(path, "reviews")
    with ReviewStore.create(path) as store:
        store.add(CLAIM, FP, Verdict.REJECTED, "no", "me", now=NOW, valid_until=date(2027, 1, 1))
        got = store.list_reviews()
    assert version(path) == 2
    assert [(r.verdict.value, r.valid_until) for r in got] == [
        ("confirmed", None),
        ("rejected", date(2027, 1, 1)),
    ]
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE reviews SET note = 'x'")  # still append-only after the upgrade
    conn.close()


def test_a_version_1_outcomes_file_reads_and_upgrades_like_reviews(tmp_path: Path) -> None:
    path = tmp_path / "o.sqlite3"
    v1_file(path, "outcomes")
    with OutcomeStore.open_existing(path) as store:
        (old,) = store.list_outcomes()
    assert old.valid_until is None and version(path) == 1
    with OutcomeStore.create(path) as store:
        store.add(CLAIM, FP, OutcomeSource.CHECK, OutcomeResult.FAILED, "d", "me", now=NOW)
        assert [o.previous_id for o in store.list_outcomes()] == [None, 1]
    assert version(path) == 2


def test_a_newer_file_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite3"
    with ReviewStore.create(path):
        pass
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 3")
    conn.close()
    with pytest.raises(ReviewStoreError, match="newer"):
        ReviewStore.create(path)


@pytest.mark.parametrize("text", ["2026-13-01", "tomorrow", "2026/12/01", ""])
def test_a_malformed_date_flag_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    argv = [
        "experience", "review", CLAIM, "--verdict", "confirmed", "--note", "n", "--by", "me",
        "--root", str(tmp_path), "--graph", str(tmp_path / "g"), "--reviews", str(tmp_path / "r"),
        "--valid-until", text,
    ]  # fmt: skip
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
