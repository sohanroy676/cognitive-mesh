"""SQLite persistence for run traces (ADR-0011). The only module that imports `sqlite3`.

Rules: a run is committed as `running` before it starts and finished in one transaction, so a crash
reads back as `interrupted`. Every row is validated on read; status is recomputed from the steps.
Error messages never contain SQL or stored values.
"""

import contextlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import TypeAdapter, ValidationError

from cognitive_mesh.agents import AgentResult, AgentSuccess
from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.traces.models import (
    AttemptTrace,
    RunSummary,
    RunTrace,
    StepTrace,
    TraceEnvironment,
    TraceListing,
    TraceNotFoundError,
    TraceRecordError,
    TraceStatus,
    TraceStoreError,
)

# Migrations (ADR-0011 amendment): MIGRATIONS[n] upgrades a version-n database to n + 1. Each script
# is plain statements (no BEGIN/COMMIT/user_version); the runner wraps it with the version bump in
# one transaction. Version 1 is the baseline `_SCHEMA`. Scripts are append-only once released.
MIGRATIONS: dict[int, str] = {}
SCHEMA_VERSION = 1 + len(MIGRATIONS)
_RESULT = TypeAdapter(AgentResult)
_BASELINE_VERSION = 1
_FINISHED = frozenset({TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.INCOMPLETE})

_SCHEMA = """
BEGIN;
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    status TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    plan_json TEXT NOT NULL,
    source_root TEXT,
    source_fingerprint TEXT,
    env_json TEXT NOT NULL,
    redactions_json TEXT NOT NULL
);
CREATE TABLE steps (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_id TEXT,
    attempt_count INTEGER NOT NULL,
    failure_kind TEXT,
    message TEXT,
    blocked_by_json TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, step_id)
);
CREATE TABLE attempts (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    agent_task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    result_status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    model_provider TEXT,
    model_name TEXT,
    basis TEXT,
    PRIMARY KEY (run_id, step_id, attempt_no),
    FOREIGN KEY (run_id, step_id) REFERENCES steps(run_id, step_id)
);
PRAGMA user_version = 1;
COMMIT;
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _result_columns(result: AgentResult) -> tuple[str | None, str | None, str | None]:
    """(model_provider, model_name, basis), derived from the result so they can be cross-checked."""
    if isinstance(result, AgentSuccess):
        model = result.model
        return (
            model.provider if model else None,
            model.model if model else None,
            result.basis.value,
        )
    return None, None, None


class TraceStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def create(cls, path: Path) -> Self:
        """Open for writing; creates the file and schema if the database is new."""
        conn = _connect(path)
        try:
            version = _user_version(conn)
            if version == 0:
                if conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                    raise TraceStoreError("the file is a database but not a trace database")
                conn.executescript(_SCHEMA)
                version = _BASELINE_VERSION
            if version > _target_version():
                raise TraceStoreError(
                    f"the trace database has version {version}, newer than this program supports"
                )
            _migrate(conn, version)
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            conn.close()
            raise TraceStoreError("cannot use the trace database (sqlite error)") from None
        except TraceStoreError:
            conn.close()
            raise
        return cls(conn)

    @classmethod
    def open_existing(cls, path: Path) -> Self:
        """Open read-only. Never creates a database."""
        if not path.is_file():
            raise TraceStoreError("the trace database does not exist")
        conn = _connect(path)
        try:
            conn.execute("PRAGMA query_only = ON")
            version = _user_version(conn)
            if 0 < version < _target_version():
                raise TraceStoreError(
                    f"the trace database has an older version ({version}); "
                    "run with --trace-db to upgrade it"
                )
            if version != _target_version():
                raise TraceStoreError(
                    f"not a trace database, or an unsupported version ({version})"
                )
        except sqlite3.Error:
            conn.close()
            raise TraceStoreError(
                "cannot read the trace database (not a SQLite database?)"
            ) from None
        except TraceStoreError:
            conn.close()
            raise
        return cls(conn)

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

    # -- writing ---------------------------------------------------------------------------

    def begin_run(self, header: RunTrace) -> None:
        """Commit the run header as `running`. `header` must be interrupted-status with no steps."""
        if header.status is not TraceStatus.INTERRUPTED or header.steps:
            raise ValueError("a run header must have interrupted status and no steps")
        try:
            self._conn.execute(
                "INSERT INTO runs (run_id, task_id, state, status, started_at, finished_at,"
                " plan_json, source_root, source_fingerprint, env_json, redactions_json)"
                " VALUES (?, ?, 'running', NULL, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    header.run_id,
                    header.task_id,
                    header.started_at.isoformat(),
                    json.dumps(header.plan),
                    header.source_root,
                    header.source_fingerprint,
                    header.environment.model_dump_json(),
                    json.dumps(list(header.redactions)),
                ),
            )
        except sqlite3.Error:
            raise TraceStoreError("cannot record the run (sqlite error)") from None

    def finish_run(
        self,
        run_id: str,
        status: TraceStatus,
        finished_at: datetime,
        redactions: Sequence[str],
        steps: Sequence[StepTrace],
    ) -> None:
        """Write steps, attempts and the final status in one transaction, or nothing."""
        if status not in _FINISHED:
            raise ValueError("a finished run must be succeeded, failed or incomplete")
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE runs SET state = 'finished', status = ?, finished_at = ?,"
                " redactions_json = ? WHERE run_id = ? AND state = 'running'",
                (status.value, finished_at.isoformat(), json.dumps(list(redactions)), run_id),
            )
            if cur.rowcount != 1:
                raise TraceStoreError("the run is not open for finishing")
            for step in steps:
                conn.execute(
                    "INSERT INTO steps (run_id, seq, step_id, capability, status, agent_id,"
                    " attempt_count, failure_kind, message, blocked_by_json, started_at,"
                    " finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        step.seq,
                        step.step_id,
                        step.capability,
                        step.status.value,
                        step.agent_id,
                        step.attempt_count,
                        step.failure_kind,
                        step.message,
                        json.dumps(list(step.blocked_by)),
                        _iso(step.started_at),
                        _iso(step.finished_at),
                    ),
                )
                for attempt in step.attempts:
                    provider, model, basis = _result_columns(attempt.result)
                    conn.execute(
                        "INSERT INTO attempts (run_id, step_id, attempt_no, agent_task_id,"
                        " agent_id, result_status, result_json, model_provider, model_name,"
                        " basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            run_id,
                            step.step_id,
                            attempt.attempt_no,
                            attempt.agent_task_id,
                            attempt.agent_id,
                            attempt.result.status,
                            attempt.result.model_dump_json(),
                            provider,
                            model,
                            basis,
                        ),
                    )
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError(
                "cannot finish the run (sqlite error); nothing was written"
            ) from None
        except TraceStoreError:
            _rollback(conn)
            raise

    # -- reading ---------------------------------------------------------------------------

    def get(self, run_id: str) -> RunTrace:
        try:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise TraceNotFoundError("no run has this id")
            steps = self._conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
            attempts = self._conn.execute(
                "SELECT * FROM attempts WHERE run_id = ? ORDER BY step_id, attempt_no", (run_id,)
            ).fetchall()
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        return _build_run(row, steps, attempts)

    def list(self) -> TraceListing:
        """All readable runs, newest first; unreadable ones are reported, never hidden."""
        try:
            ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT run_id FROM runs ORDER BY started_at DESC, run_id"
                )
            ]
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        runs: list[RunSummary] = []
        malformed: list[str] = []
        for run_id in ids:
            try:
                trace = self.get(run_id)
            except TraceRecordError:
                malformed.append(run_id)
                continue
            runs.append(
                RunSummary(
                    run_id=trace.run_id,
                    task_id=trace.task_id,
                    status=trace.status,
                    started_at=trace.started_at,
                    step_count=len(trace.steps),
                )
            )
        return TraceListing(runs=tuple(runs), malformed=tuple(malformed))


def _connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(str(path), isolation_level=None)
    except sqlite3.Error:
        raise TraceStoreError("cannot open the trace database (sqlite error)") from None
    conn.row_factory = sqlite3.Row
    return conn


def _target_version() -> int:
    return _BASELINE_VERSION + len(MIGRATIONS)


def _migrate(conn: sqlite3.Connection, version: int) -> None:
    """Apply MIGRATIONS from `version` up to the target; each step is one atomic transaction."""
    while version < _target_version():
        script = MIGRATIONS.get(version)
        if script is None:
            raise TraceStoreError(f"no migration from trace database version {version}")
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
            )
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot upgrade the trace database (sqlite error)") from None
        version += 1


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        conn.execute("ROLLBACK")


def _build_run(
    row: sqlite3.Row, step_rows: Sequence[sqlite3.Row], attempt_rows: Sequence[sqlite3.Row]
) -> RunTrace:
    run_id = str(row["run_id"])

    def bad(reason: str) -> TraceRecordError:
        return TraceRecordError(f"run {run_id}: {reason}")

    try:
        state = row["state"]
        stored = row["status"]
        plan = json.loads(row["plan_json"])
        redactions = json.loads(row["redactions_json"])
        if not isinstance(plan, dict) or not isinstance(redactions, list):
            raise bad("plan or redactions have the wrong shape")
        environment = TraceEnvironment.model_validate_json(row["env_json"])
        task_id = row["task_id"]

        if state == "running":
            if stored is not None or row["finished_at"] is not None or step_rows or attempt_rows:
                raise bad("an unfinished run has finish data")
            status = TraceStatus.INTERRUPTED
        elif state == "finished":
            if row["finished_at"] is None:
                raise bad("a finished run has no finish time")
            status = TraceStatus(stored)
            if status not in _FINISHED:
                raise bad("a finished run has an unfinished status")
        else:
            raise bad("unknown run state")

        by_step: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for a in attempt_rows:
            by_step[a["step_id"]].append(a)
        step_ids = {s["step_id"] for s in step_rows}
        if set(by_step) - step_ids:
            raise bad("attempts refer to unknown steps")

        steps = tuple(
            _build_step(task_id, index, s, by_step.get(s["step_id"], []), bad)
            for index, s in enumerate(step_rows)
        )
        trace = RunTrace(
            run_id=run_id,
            task_id=task_id,
            status=status,
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            source_root=row["source_root"],
            source_fingerprint=row["source_fingerprint"],
            environment=environment,
            redactions=tuple(str(r) for r in redactions),
            plan=plan,
            steps=steps,
        )
        if status is not TraceStatus.INTERRUPTED and trace.to_task_run().status.value != status:
            raise bad("the stored status disagrees with the steps")
    except TraceRecordError:
        raise
    except ValidationError as exc:
        kinds = ", ".join(sorted({str(e["type"]) for e in exc.errors()}))
        raise bad(f"invalid record ({kinds})") from None
    except (ValueError, TypeError, KeyError) as exc:
        raise bad(f"invalid record ({type(exc).__name__})") from None
    return trace


def _build_step(
    task_id: str,
    index: int,
    s: sqlite3.Row,
    attempt_rows: Sequence[sqlite3.Row],
    fail: Callable[[str], TraceRecordError],
) -> StepTrace:
    step_id = s["step_id"]
    if s["seq"] != index:
        raise fail("steps are out of sequence")
    attempts: list[AttemptTrace] = []
    for n, a in enumerate(attempt_rows, start=1):
        result = _RESULT.validate_json(a["result_json"])
        expected_task = f"{task_id}.{step_id}.{n}"
        if (
            a["attempt_no"] != n
            or a["agent_task_id"] != expected_task
            or result.task_id != expected_task
            or a["agent_id"] != result.agent_id
            or result.capability != s["capability"]
            or a["result_status"] != result.status
            or (a["model_provider"], a["model_name"], a["basis"]) != _result_columns(result)
        ):
            raise fail("an attempt does not match its step or task")
        attempts.append(
            AttemptTrace(
                attempt_no=n, agent_task_id=expected_task, agent_id=result.agent_id, result=result
            )
        )
    # A fallback agent may have taken over, so only the last attempt must match the step's agent.
    if attempts and s["agent_id"] is not None and s["agent_id"] != attempts[-1].agent_id:
        raise fail("an attempt does not match its step or task")
    if len(attempts) > s["attempt_count"]:
        raise fail("a step has more attempts than its attempt count")
    if any(isinstance(a.result, AgentSuccess) for a in attempts[:-1]):
        raise fail("a step continued after a successful attempt")
    last_ok = bool(attempts) and isinstance(attempts[-1].result, AgentSuccess)
    if (s["status"] == StepStatus.SUCCEEDED.value) != last_ok:
        raise fail("a step's status disagrees with its attempts")
    blocked_by = json.loads(s["blocked_by_json"])
    if not isinstance(blocked_by, list):
        raise fail("blocked_by has the wrong shape")
    return StepTrace(
        seq=s["seq"],
        step_id=step_id,
        capability=s["capability"],
        status=s["status"],
        agent_id=s["agent_id"],
        attempt_count=s["attempt_count"],
        failure_kind=s["failure_kind"],
        message=s["message"],
        blocked_by=tuple(str(b) for b in blocked_by),
        started_at=s["started_at"],
        finished_at=s["finished_at"],
        attempts=tuple(attempts),
    )
