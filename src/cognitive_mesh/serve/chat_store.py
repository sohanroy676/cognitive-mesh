"""The chat file (ADR-0044): sessions, turns, approval requests and append-only turn events.

A separate SQLite file with its own `application_id` and schema version. The status of a turn is
derived from its events and from whether a worker of this process still runs it, never stored:
a turn that has no closing event and no live worker reads back `interrupted`. Text is stored
after redaction.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from cognitive_mesh.ingestion.secrets import redact_secrets

APPLICATION_ID: Final = 0x43484154  # "CHAT"
SCHEMA_VERSION: Final = 1
APPROVAL_TTL: Final = timedelta(hours=12)
MAX_EVENT_JSON: Final = 60_000
CLOSING_KINDS: Final = frozenset({"done", "failed", "rejected", "declined"})
MAX_LINE: Final = 2_000

SCHEMA = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY, project TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE turns (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
    project TEXT NOT NULL, text TEXT NOT NULL, memory INTEGER NOT NULL,
    mode TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE turn_events (
    id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES turns(id),
    seq INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (turn_id, seq)
);
CREATE TABLE approvals (
    id TEXT PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES turns(id), action TEXT NOT NULL,
    change_id TEXT NOT NULL, diff_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, used_at TEXT, used_for TEXT
);
"""


class ChatStoreError(Exception):
    """A fixed-text failure of the chat file."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def clean_text(text: str, limit: int = MAX_LINE) -> str:
    """Redacted, cut to `limit`; long unbroken runs are cut first because redaction is quadratic."""
    cut = "\n".join(line[:500] for line in text.splitlines())[: limit * 2]
    redacted, _ = redact_secrets(cut)
    return redacted[:limit]


class ChatStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._live: set[int] = set()
        self._boot = _now()  # an approval request made before this moment belongs to a dead process
        self._conn = self._open(path)

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
            app = conn.execute("PRAGMA application_id").fetchone()[0]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if app == 0 and version == 0:
                tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
                if tables:
                    raise ChatStoreError("the file is not a chat file")
                conn.executescript("BEGIN;" + SCHEMA)
                conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
            elif app != APPLICATION_ID:
                raise ChatStoreError("the file is not a chat file")
            elif version != SCHEMA_VERSION:
                raise ChatStoreError("the chat file has an unsupported version")
            return conn
        except sqlite3.Error:
            raise ChatStoreError("cannot open the chat file") from None

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # -- sessions and turns ----------------------------------------------------------------

    def new_session(self, project: str) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO sessions (project, created_at) VALUES (?, ?)", (project, _now())
            )
            return int(cur.lastrowid or 0)

    def session_exists(self, session_id: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,))
            return row.fetchone() is not None

    def begin_turn(self, session_id: int, project: str, text: str, memory: bool, mode: str) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO turns (session_id, project, text, memory, mode, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, project, clean_text(text, 4000), int(memory), mode, _now()),
            )
            turn_id = int(cur.lastrowid or 0)
            self._live.add(turn_id)
            return turn_id

    def end_worker(self, turn_id: int) -> None:
        with self._lock:
            self._live.discard(turn_id)

    def running_turn(self) -> int | None:
        with self._lock:
            return next(iter(sorted(self._live)), None)

    def add_event(self, turn_id: int, kind: str, data: dict[str, Any]) -> int:
        text = json.dumps(data, ensure_ascii=True, sort_keys=True)
        if len(text) > MAX_EVENT_JSON:
            text = json.dumps({"truncated": True, "note": "the event was too large to store"})
        with self._tx() as c:
            seq = c.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM turn_events WHERE turn_id = ?", (turn_id,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO turn_events (turn_id, seq, kind, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (turn_id, seq, kind, text, _now()),
            )
            return int(seq)

    def turn(self, turn_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, session_id, project, text, memory, created_at, mode "
                "FROM turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            if row is None:
                return None
            kinds = [
                r[0]
                for r in self._conn.execute(
                    "SELECT kind FROM turn_events WHERE turn_id = ? ORDER BY seq", (turn_id,)
                )
            ]
            live = turn_id in self._live
            waiting = self._conn.execute(
                "SELECT 1 FROM approvals WHERE turn_id = ? AND used_at IS NULL "
                "AND expires_at > ? AND created_at >= ?",
                (turn_id, _now(), self._boot),
            ).fetchone()
        return {
            "id": row[0],
            "session_id": row[1],
            "project": row[2],
            "text": row[3],
            "memory": bool(row[4]),
            "mode": row[6],
            "created_at": row[5],
            "status": derive_status(kinds, live, waiting is not None),
        }

    def events(self, turn_id: int, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, kind, data, created_at FROM turn_events "
                "WHERE turn_id = ? AND seq > ? ORDER BY seq",
                (turn_id, after),
            ).fetchall()
        return [
            {"seq": r[0], "kind": r[1], "data": json.loads(r[2]), "created_at": r[3]} for r in rows
        ]

    def session_turns(self, session_id: int) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM turns WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.project, s.created_at, (SELECT count(*) FROM turns t "
                "WHERE t.session_id = s.id), (SELECT t.text FROM turns t WHERE t.session_id = s.id "
                "ORDER BY t.id LIMIT 1) FROM sessions s ORDER BY s.id DESC LIMIT 50"
            ).fetchall()
        return [
            {"id": r[0], "project": r[1], "created_at": r[2], "turns": r[3], "first": r[4] or ""}
            for r in rows
        ]

    # -- approvals -------------------------------------------------------------------------

    def open_approval(self, turn_id: int, action: str, change_id: str, diff_sha256: str) -> str:
        approval_id = secrets.token_hex(16)
        now = datetime.now(UTC)
        with self._tx() as c:
            c.execute(
                "INSERT INTO approvals (id, turn_id, action, change_id, diff_sha256, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    turn_id,
                    action,
                    change_id,
                    diff_sha256,
                    now.isoformat(),
                    (now + APPROVAL_TTL).isoformat(),
                ),
            )
        return approval_id

    def use_approval(
        self, approval_id: str, action: str, change_id: str, diff_sha256: str, used_for: str
    ) -> tuple[bool, str, int | None]:
        """Claim an approval once. Returns (ok, fixed reason, turn id). A replay is refused."""
        with self._tx() as c:
            row = c.execute(
                "SELECT action, change_id, diff_sha256, expires_at, used_at, created_at, turn_id "
                "FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return False, "no such approval request", None
            turn_id = int(row[6])
            if row[4] is not None:
                return False, "this approval request was already used", turn_id
            if datetime.fromisoformat(row[3]) < datetime.now(UTC):
                return False, "this approval request has expired", turn_id
            if row[5] < self._boot:
                return False, "this approval request expired when the server restarted", turn_id
            if (row[0], row[1], row[2]) != (action, change_id, diff_sha256):
                return False, "the approval request is for a different action or diff", turn_id
            c.execute(
                "UPDATE approvals SET used_at = ?, used_for = ? WHERE id = ? AND used_at IS NULL",
                (_now(), used_for, approval_id),
            )
            return True, "", turn_id

    def approval_turn(self, approval_id: str) -> int | None:
        """The turn an approval request belongs to (read only; it does not use the request)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT turn_id FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def open_approvals(self, turn_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, action, change_id, diff_sha256 FROM approvals WHERE turn_id = ? "
                "AND used_at IS NULL AND expires_at > ? AND created_at >= ? ORDER BY created_at",
                (turn_id, _now(), self._boot),
            ).fetchall()
        return [{"id": r[0], "action": r[1], "change_id": r[2], "diff_sha256": r[3]} for r in rows]

    def approval_rows(self) -> int:
        with closing(self._conn.execute("SELECT count(*) FROM approvals")) as cur:
            return int(cur.fetchone()[0])


def derive_status(kinds: list[str], live: bool, awaiting: bool = False) -> str:
    """`awaiting` is true only for an unused, unexpired request made since this process started."""
    for kind in reversed(kinds):
        if kind in CLOSING_KINDS:
            return kind
    if awaiting:
        return "awaiting_approval"
    return "running" if live else "interrupted"
