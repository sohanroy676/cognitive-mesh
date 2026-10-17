"""Human reviews of claims (ADR-0016). The only module that imports `sqlite3` for the reviews file.

A review is a person's assertion about one claim on one code version (fingerprint). The file is
append-only: no update or delete API exists, and database triggers refuse both. It is separate from
the graph file because `experience build` rewrites every graph table, and from the trace database
because validated knowledge must not sit next to raw traces.
"""

import contextlib
import re
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from cognitive_mesh.ingestion.secrets import redact_secrets

SCHEMA_VERSION: Final = 1
APPLICATION_ID: Final = 0x434D4552  # "CMER": tells a reviews file from any other SQLite file
MAX_NOTE_CHARS: Final = 1000
MAX_REVIEWER_CHARS: Final = 100

_CLAIM_ID = re.compile(r"claim:[0-9a-f]{32}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")

_SCHEMA: Final = f"""
PRAGMA application_id = {APPLICATION_ID};
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('confirmed', 'rejected')),
    note TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX reviews_by_claim ON reviews (claim_id);
CREATE TRIGGER reviews_no_update BEFORE UPDATE ON reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
CREATE TRIGGER reviews_no_delete BEFORE DELETE ON reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
PRAGMA user_version = {SCHEMA_VERSION};
"""


class ReviewStoreError(Exception):
    """The reviews file or a review cannot be used. Messages never contain stored or given text."""


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Review(BaseModel):
    """One stored review. `fingerprint` is the code version the reviewer looked at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    claim_id: str
    fingerprint: str
    verdict: Verdict
    note: str
    reviewer: str
    created_at: datetime


def _clean(text: str, limit: int, what: str) -> str:
    """Reject control characters and over-long text, then redact secrets."""
    if not text.strip():
        raise ReviewStoreError(f"the review {what} is empty")
    if len(text) > limit:
        raise ReviewStoreError(f"the review {what} is longer than {limit} characters")
    if not text.isprintable():
        raise ReviewStoreError(f"the review {what} contains control characters")
    redacted, _ = redact_secrets(text.strip())
    return redacted


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


class ReviewStore:
    def __init__(self, conn: sqlite3.Connection, *, writable: bool) -> None:
        self._conn = conn
        self._writable = writable

    @classmethod
    def create(cls, path: Path) -> Self:
        """Open for writing; a missing file is created, any other file must be a reviews file."""
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error:
            raise ReviewStoreError("cannot open the reviews file (sqlite error)") from None
        try:
            fresh = (
                _pragma_int(conn, "application_id") == 0
                and not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            )
            if fresh:
                conn.executescript(_SCHEMA)
            else:
                cls._check_header(conn)
        except sqlite3.Error:
            conn.close()
            raise ReviewStoreError("cannot open the reviews file (sqlite error)") from None
        except ReviewStoreError:
            conn.close()
            raise
        return cls(conn, writable=True)

    @classmethod
    def open_existing(cls, path: Path) -> Self:
        """Open read-only; never creates a file."""
        if not path.is_file():
            raise ReviewStoreError("the reviews file does not exist")
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
            conn.execute("PRAGMA query_only = ON")
            cls._check_header(conn)
        except sqlite3.Error:
            raise ReviewStoreError("cannot open the reviews file (sqlite error)") from None
        except ReviewStoreError:
            conn.close()
            raise
        return cls(conn, writable=False)

    @staticmethod
    def _check_header(conn: sqlite3.Connection) -> None:
        if _pragma_int(conn, "application_id") != APPLICATION_ID:
            raise ReviewStoreError("the file is not a reviews file")
        version = _pragma_int(conn, "user_version")
        if version > SCHEMA_VERSION:
            raise ReviewStoreError(
                f"the reviews file has version {version}, newer than this program supports"
            )
        if version != SCHEMA_VERSION:
            raise ReviewStoreError(f"unsupported reviews file version ({version})")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def add(
        self,
        claim_id: str,
        fingerprint: str,
        verdict: Verdict,
        note: str,
        reviewer: str,
        *,
        now: datetime | None = None,
    ) -> Review:
        if not self._writable:
            raise ReviewStoreError("the reviews file was opened read-only")
        if not _CLAIM_ID.fullmatch(claim_id):
            raise ReviewStoreError("the claim id is not a claim id")
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise ReviewStoreError("the fingerprint is not a sha256 hex digest")
        clean_note = _clean(note, MAX_NOTE_CHARS, "note")
        clean_by = _clean(reviewer, MAX_REVIEWER_CHARS, "reviewer")
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "INSERT INTO reviews (claim_id, fingerprint, verdict, note, reviewer,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        claim_id,
                        fingerprint,
                        verdict.value,
                        clean_note,
                        clean_by,
                        stamp.isoformat(),
                    ),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("ROLLBACK")
                raise
        except sqlite3.Error:
            raise ReviewStoreError("cannot write the review (sqlite error)") from None
        return Review(
            id=int(cur.lastrowid or 0),
            claim_id=claim_id,
            fingerprint=fingerprint,
            verdict=verdict,
            note=clean_note,
            reviewer=clean_by,
            created_at=stamp,
        )

    def list_reviews(self, claim_id: str | None = None) -> list[Review]:
        """Oldest first. A malformed row is an error, never skipped."""
        try:
            if claim_id is None:
                rows = self._conn.execute(
                    "SELECT id, claim_id, fingerprint, verdict, note, reviewer, created_at"
                    " FROM reviews ORDER BY id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, claim_id, fingerprint, verdict, note, reviewer, created_at"
                    " FROM reviews WHERE claim_id = ? ORDER BY id",
                    (claim_id,),
                ).fetchall()
        except sqlite3.Error:
            raise ReviewStoreError("cannot read the reviews (sqlite error)") from None
        try:
            return [
                Review(
                    id=r[0],
                    claim_id=r[1],
                    fingerprint=r[2],
                    verdict=r[3],
                    note=r[4],
                    reviewer=r[5],
                    created_at=datetime.fromisoformat(r[6]),
                )
                for r in rows
            ]
        except (ValidationError, ValueError):
            raise ReviewStoreError("the reviews file holds a malformed review") from None
