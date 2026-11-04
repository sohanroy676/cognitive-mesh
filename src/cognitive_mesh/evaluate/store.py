"""The evaluation file (ADR-0043). The only module that imports `sqlite3` for it.

It records which task ran in which arm and repeat, and where its change and runs are. It holds no
result of a check and no model text: those stay in each arm's own trace database and the report
reads them from there. Rows are append-only (triggers refuse update and delete). An item is begun
first and finished later in a second table, so an item whose process died reads back as
`interrupted`; nothing is ever dropped.
"""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict

from cognitive_mesh.evaluate.errors import EvalError

SCHEMA_VERSION: Final = 1
APPLICATION_ID: Final = 0x434D4556  # "CMEV": tells an evaluation file from any other SQLite file

_SCHEMA: Final = f"""
PRAGMA application_id = {APPLICATION_ID};
CREATE TABLE evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taskset_name TEXT NOT NULL,
    taskset_sha256 TEXT NOT NULL,
    model_entry TEXT NOT NULL,
    catalog_sha256 TEXT,
    repeats INTEGER NOT NULL,
    task_count INTEGER NOT NULL,
    checks_allowed INTEGER NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE eval_finished (
    eval_id INTEGER PRIMARY KEY REFERENCES evals (id),
    finished_at TEXT NOT NULL
);
CREATE TABLE eval_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_id INTEGER NOT NULL REFERENCES evals (id),
    repeat INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    arm TEXT NOT NULL CHECK (arm IN ('off', 'on')),
    started_at TEXT NOT NULL,
    claims_offered INTEGER NOT NULL,
    past_changes_offered INTEGER NOT NULL,
    prep_s REAL NOT NULL,
    UNIQUE (eval_id, repeat, task_id, arm)
);
CREATE TABLE eval_item_results (
    item_id INTEGER PRIMARY KEY REFERENCES eval_items (id),
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('finished', 'aborted')),
    change_id TEXT,
    run_ids_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    refresh_s REAL NOT NULL
);
CREATE TRIGGER evals_no_update BEFORE UPDATE ON evals
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
CREATE TRIGGER evals_no_delete BEFORE DELETE ON evals
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
CREATE TRIGGER items_no_update BEFORE UPDATE ON eval_items
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
CREATE TRIGGER items_no_delete BEFORE DELETE ON eval_items
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
CREATE TRIGGER results_no_update BEFORE UPDATE ON eval_item_results
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
CREATE TRIGGER results_no_delete BEFORE DELETE ON eval_item_results
BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
PRAGMA user_version = {SCHEMA_VERSION};
"""


class Arm(StrEnum):
    OFF = "off"
    ON = "on"


class ItemState(StrEnum):
    FINISHED = "finished"  # a change was made (it may still have failed its check)
    ABORTED = "aborted"  # the task stopped before a change existed; `reason` says why
    INTERRUPTED = "interrupted"  # begun, never finished (derived on read, never stored)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvalHeader(_Frozen):
    id: int
    taskset_name: str
    taskset_sha256: str
    model_entry: str
    catalog_sha256: str | None
    repeats: int
    task_count: int
    checks_allowed: bool
    started_at: datetime
    finished_at: datetime | None


class EvalItem(_Frozen):
    id: int
    eval_id: int
    repeat: int
    task_id: str
    arm: Arm
    started_at: datetime
    claims_offered: int
    past_changes_offered: int
    prep_s: float
    state: ItemState
    finished_at: datetime | None = None
    change_id: str | None = None
    run_ids: tuple[str, ...] = ()
    reason: str = ""
    refresh_s: float = 0.0


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


class EvalStore:
    def __init__(self, conn: sqlite3.Connection, *, writable: bool) -> None:
        self._conn = conn
        self._writable = writable

    @classmethod
    def create(cls, path: Path) -> Self:
        """Open for writing; a missing file is created, any other must be an evaluation file."""
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error:
            raise EvalError("cannot open the evaluation file (sqlite error)") from None
        try:
            fresh = (
                _pragma_int(conn, "application_id") == 0
                and not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            )
            if fresh:
                conn.executescript(f"BEGIN IMMEDIATE; {_SCHEMA} COMMIT;")
            else:
                cls._check_header(conn)
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            conn.close()
            raise EvalError("cannot open the evaluation file (sqlite error)") from None
        except EvalError:
            conn.close()
            raise
        return cls(conn, writable=True)

    @classmethod
    def open_existing(cls, path: Path) -> Self:
        """Open read-only; never creates a file."""
        if not path.is_file():
            raise EvalError("the evaluation file does not exist")
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
            conn.execute("PRAGMA query_only = ON")
            cls._check_header(conn)
        except sqlite3.Error:
            raise EvalError("cannot open the evaluation file (sqlite error)") from None
        except EvalError:
            conn.close()
            raise
        return cls(conn, writable=False)

    @staticmethod
    def _check_header(conn: sqlite3.Connection) -> None:
        if _pragma_int(conn, "application_id") != APPLICATION_ID:
            raise EvalError("the file is not an evaluation file")
        if _pragma_int(conn, "user_version") != SCHEMA_VERSION:
            raise EvalError("the evaluation file has an unsupported version")

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

    def _write(self, sql: str, params: tuple[object, ...]) -> int:
        if not self._writable:
            raise EvalError("the evaluation file was opened read-only")
        try:
            cur = self._conn.execute(sql, params)
        except sqlite3.IntegrityError:
            raise EvalError("that evaluation record already exists or is not valid") from None
        except sqlite3.Error:
            raise EvalError("cannot write the evaluation file (sqlite error)") from None
        return int(cur.lastrowid or 0)

    # -- writing ---------------------------------------------------------------------------

    def begin_eval(
        self,
        *,
        taskset_name: str,
        taskset_sha256: str,
        model_entry: str,
        catalog_sha256: str | None,
        repeats: int,
        task_count: int,
        checks_allowed: bool,
        now: datetime,
    ) -> int:
        return self._write(
            "INSERT INTO evals (taskset_name, taskset_sha256, model_entry, catalog_sha256,"
            " repeats, task_count, checks_allowed, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                taskset_name,
                taskset_sha256,
                model_entry,
                catalog_sha256,
                repeats,
                task_count,
                int(checks_allowed),
                _stamp(now),
            ),
        )

    def finish_eval(self, eval_id: int, now: datetime) -> None:
        self._write(
            "INSERT INTO eval_finished (eval_id, finished_at) VALUES (?, ?)",
            (eval_id, _stamp(now)),
        )

    def begin_item(
        self,
        eval_id: int,
        repeat: int,
        task_id: str,
        arm: Arm,
        *,
        claims_offered: int,
        past_changes_offered: int,
        prep_s: float,
        now: datetime,
    ) -> int:
        return self._write(
            "INSERT INTO eval_items (eval_id, repeat, task_id, arm, started_at, claims_offered,"
            " past_changes_offered, prep_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eval_id,
                repeat,
                task_id,
                arm.value,
                _stamp(now),
                claims_offered,
                past_changes_offered,
                prep_s,
            ),
        )

    def finish_item(
        self,
        item_id: int,
        *,
        aborted: bool,
        change_id: str | None,
        run_ids: tuple[str, ...],
        reason: str,
        refresh_s: float,
        now: datetime,
    ) -> None:
        self._write(
            "INSERT INTO eval_item_results (item_id, finished_at, status, change_id,"
            " run_ids_json, reason, refresh_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                _stamp(now),
                ItemState.ABORTED.value if aborted else ItemState.FINISHED.value,
                change_id,
                json.dumps(list(run_ids)),
                reason,
                refresh_s,
            ),
        )

    # -- reading ---------------------------------------------------------------------------

    def evals(self) -> list[EvalHeader]:
        try:
            rows = self._conn.execute(
                "SELECT e.*, f.finished_at AS finished_at FROM evals e"
                " LEFT JOIN eval_finished f ON f.eval_id = e.id ORDER BY e.id"
            ).fetchall()
        except sqlite3.Error:
            raise EvalError("cannot read the evaluation file (sqlite error)") from None
        out = []
        for r in rows:
            try:
                out.append(
                    EvalHeader(
                        id=r[0],
                        taskset_name=r[1],
                        taskset_sha256=r[2],
                        model_entry=r[3],
                        catalog_sha256=r[4],
                        repeats=r[5],
                        task_count=r[6],
                        checks_allowed=bool(r[7]),
                        started_at=datetime.fromisoformat(r[8]),
                        finished_at=datetime.fromisoformat(r[9]) if r[9] else None,
                    )
                )
            except ValueError:
                raise EvalError("the evaluation file holds a malformed record") from None
        return out

    def items(self, eval_id: int) -> list[EvalItem]:
        try:
            rows = self._conn.execute(
                "SELECT i.id, i.repeat, i.task_id, i.arm, i.started_at, i.claims_offered,"
                " i.past_changes_offered, i.prep_s, r.finished_at, r.status, r.change_id,"
                " r.run_ids_json, r.reason, r.refresh_s"
                " FROM eval_items i LEFT JOIN eval_item_results r ON r.item_id = i.id"
                " WHERE i.eval_id = ? ORDER BY i.repeat, i.task_id, i.arm",
                (eval_id,),
            ).fetchall()
        except sqlite3.Error:
            raise EvalError("cannot read the evaluation file (sqlite error)") from None
        out = []
        for r in rows:
            try:
                state = ItemState(r[9]) if r[9] is not None else ItemState.INTERRUPTED
                out.append(
                    EvalItem(
                        id=r[0],
                        eval_id=eval_id,
                        repeat=r[1],
                        task_id=r[2],
                        arm=Arm(r[3]),
                        started_at=datetime.fromisoformat(r[4]),
                        claims_offered=r[5],
                        past_changes_offered=r[6],
                        prep_s=r[7],
                        state=state,
                        finished_at=datetime.fromisoformat(r[8]) if r[8] else None,
                        change_id=r[10],
                        run_ids=tuple(json.loads(r[11])) if r[11] else (),
                        reason=r[12] or "",
                        refresh_s=r[13] or 0.0,
                    )
                )
            except (ValueError, TypeError):
                raise EvalError("the evaluation file holds a malformed record") from None
        return out
