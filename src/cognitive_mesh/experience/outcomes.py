"""Outcome evidence for claims (ADR-0018). The only module that imports `sqlite3` for outcomes.

An outcome says whether the work a claim supported turned out right (`succeeded`), wrong
(`failed`) or unclear (`ambiguous`). It comes from a check the person ran and recorded, or from a
person's own account. A run's own success is never an outcome. A `promotion` row records a refused
promotion (`rejected`); it is shown but never changes a status.

The file is append-only: triggers refuse update and delete. Each row may name the previous row for
the same claim and code version, so a claim's history is a chain that can be followed back.
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
APPLICATION_ID: Final = 0x434F5554  # "COUT": tells an outcomes file from any other SQLite file
MAX_DETAIL_CHARS: Final = 1000
MAX_RECORDER_CHARS: Final = 100

_CLAIM_ID = re.compile(r"claim:[0-9a-f]{32}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")

_SCHEMA: Final = f"""
PRAGMA application_id = {APPLICATION_ID};
CREATE TABLE outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('check', 'person', 'promotion')),
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed', 'ambiguous', 'rejected')),
    detail TEXT NOT NULL,
    recorder TEXT NOT NULL,
    previous_id INTEGER REFERENCES outcomes (id),
    created_at TEXT NOT NULL,
    CHECK ((source = 'promotion') = (result = 'rejected'))
);
CREATE INDEX outcomes_by_claim ON outcomes (claim_id, fingerprint);
CREATE TRIGGER outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
PRAGMA user_version = {SCHEMA_VERSION};
"""


class OutcomeStoreError(Exception):
    """The outcomes file or an outcome cannot be used. Messages hold no stored or given text."""


class OutcomeSource(StrEnum):
    CHECK = "check"
    PERSON = "person"
    PROMOTION = "promotion"


class OutcomeResult(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


EVIDENCE_RESULTS: Final = frozenset(
    {OutcomeResult.SUCCEEDED, OutcomeResult.FAILED, OutcomeResult.AMBIGUOUS}
)


class Outcome(BaseModel):
    """One stored outcome. `fingerprint` is the code version it was observed on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    claim_id: str
    fingerprint: str
    source: OutcomeSource
    result: OutcomeResult
    detail: str  # the check's command or the person's reason; data, never instructions
    recorder: str
    previous_id: int | None
    created_at: datetime


def _clean(text: str, limit: int, what: str) -> str:
    """Reject empty, control-character and over-long text, then redact secrets."""
    if not text.strip():
        raise OutcomeStoreError(f"the outcome {what} is empty")
    if len(text) > limit:
        raise OutcomeStoreError(f"the outcome {what} is longer than {limit} characters")
    if not text.isprintable():
        raise OutcomeStoreError(f"the outcome {what} contains control characters")
    redacted, _ = redact_secrets(text.strip())
    return redacted


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _is_fresh(conn: sqlite3.Connection) -> bool:
    """True for a new, empty file: no application id and no tables."""
    return (
        _pragma_int(conn, "application_id") == 0
        and not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    )


def _check_header(conn: sqlite3.Connection) -> None:
    if _pragma_int(conn, "application_id") != APPLICATION_ID:
        raise OutcomeStoreError("the file is not an outcomes file")
    version = _pragma_int(conn, "user_version")
    if version > SCHEMA_VERSION:
        raise OutcomeStoreError(
            f"the outcomes file has version {version}, newer than this program supports"
        )
    if version != SCHEMA_VERSION:
        raise OutcomeStoreError(f"unsupported outcomes file version ({version})")


class OutcomeStore:
    def __init__(self, conn: sqlite3.Connection, *, writable: bool) -> None:
        self._conn = conn
        self._writable = writable

    @classmethod
    def create(cls, path: Path) -> Self:
        """Open for writing; a missing file is created, any other file must be an outcomes file."""
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error:
            raise OutcomeStoreError("cannot open the outcomes file (sqlite error)") from None
        try:
            if _is_fresh(conn):
                cls._create_schema(conn)
            else:
                _check_header(conn)
        except sqlite3.Error:
            conn.close()
            raise OutcomeStoreError("cannot open the outcomes file (sqlite error)") from None
        except OutcomeStoreError:
            conn.close()
            raise
        return cls(conn, writable=True)

    @classmethod
    def open_existing(cls, path: Path) -> Self:
        """Open read-only; never creates a file."""
        if not path.is_file():
            raise OutcomeStoreError("the outcomes file does not exist")
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
            conn.execute("PRAGMA query_only = ON")
            _check_header(conn)
        except sqlite3.Error:
            raise OutcomeStoreError("cannot open the outcomes file (sqlite error)") from None
        except OutcomeStoreError:
            conn.close()
            raise
        return cls(conn, writable=False)

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        """Create the schema in one transaction. If another process made a valid file, use it."""
        try:
            conn.executescript(f"BEGIN IMMEDIATE; {_SCHEMA} COMMIT;")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            try:
                _check_header(conn)
            except (OutcomeStoreError, sqlite3.Error):
                raise sqlite3.Error("schema") from None

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
        source: OutcomeSource,
        result: OutcomeResult,
        detail: str,
        recorder: str,
        *,
        now: datetime | None = None,
    ) -> Outcome:
        """Append one outcome. It links to the newest row for the same claim and code version."""
        if not self._writable:
            raise OutcomeStoreError("the outcomes file was opened read-only")
        if not _CLAIM_ID.fullmatch(claim_id):
            raise OutcomeStoreError("the claim id is not a claim id")
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise OutcomeStoreError("the fingerprint is not a sha256 hex digest")
        if (source is OutcomeSource.PROMOTION) != (result is OutcomeResult.REJECTED):
            raise OutcomeStoreError("only a refused promotion is recorded as rejected")
        clean_detail = _clean(detail, MAX_DETAIL_CHARS, "detail")
        clean_by = _clean(recorder, MAX_RECORDER_CHARS, "recorder")
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                previous = self._conn.execute(
                    "SELECT max(id) FROM outcomes WHERE claim_id = ? AND fingerprint = ?",
                    (claim_id, fingerprint),
                ).fetchone()[0]
                cur = self._conn.execute(
                    "INSERT INTO outcomes (claim_id, fingerprint, source, result, detail, recorder,"
                    " previous_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        claim_id,
                        fingerprint,
                        source.value,
                        result.value,
                        clean_detail,
                        clean_by,
                        previous,
                        stamp.isoformat(),
                    ),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("ROLLBACK")
                raise
        except sqlite3.Error:
            raise OutcomeStoreError("cannot write the outcome (sqlite error)") from None
        return Outcome(
            id=int(cur.lastrowid or 0),
            claim_id=claim_id,
            fingerprint=fingerprint,
            source=source,
            result=result,
            detail=clean_detail,
            recorder=clean_by,
            previous_id=previous,
            created_at=stamp,
        )

    def list_outcomes(self, claim_id: str | None = None) -> list[Outcome]:
        """Oldest first. A malformed row is an error, never skipped."""
        columns = (
            "SELECT id, claim_id, fingerprint, source, result, detail, recorder, previous_id,"
            " created_at FROM outcomes"
        )
        try:
            if claim_id is None:
                rows = self._conn.execute(f"{columns} ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    f"{columns} WHERE claim_id = ? ORDER BY id", (claim_id,)
                ).fetchall()
        except sqlite3.Error:
            raise OutcomeStoreError("cannot read the outcomes (sqlite error)") from None
        try:
            return [
                Outcome(
                    id=r[0],
                    claim_id=r[1],
                    fingerprint=r[2],
                    source=r[3],
                    result=r[4],
                    detail=r[5],
                    recorder=r[6],
                    previous_id=r[7],
                    created_at=datetime.fromisoformat(r[8]),
                )
                for r in rows
            ]
        except (ValidationError, ValueError):
            raise OutcomeStoreError("the outcomes file holds a malformed outcome") from None
