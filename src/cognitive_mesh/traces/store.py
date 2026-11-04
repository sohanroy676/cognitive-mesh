"""SQLite persistence for run traces (ADR-0011). The only module that imports `sqlite3`.

Rules: a run is committed as `running` before it starts and finished in one transaction, so a crash
reads back as `interrupted`. Every row is validated on read; status is recomputed from the steps.
Error messages never contain SQL or stored values.
"""

import contextlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import TypeAdapter, ValidationError

from cognitive_mesh.agents import AgentResult, AgentSuccess, RecalledContext
from cognitive_mesh.change.models import ChangeEvent, ChangeRecord
from cognitive_mesh.decisions.models import (
    Citation,
    CitationKind,
    Decision,
    DecisionError,
    DecisionLink,
    DecisionListing,
    DuplicateDecisionError,
    decision_digest,
)
from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.providers.recording import CallRecord
from cognitive_mesh.traces.models import (
    AttemptTrace,
    CallListing,
    CallRow,
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
MIGRATIONS: dict[int, str] = {
    # 1 -> 2 (ADR-0017): what past experience a run was offered. One row per run that was given a
    # context pack; older runs and plain `run` calls have none.
    1: """
CREATE TABLE run_context (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    context_json TEXT NOT NULL,
    request_len INTEGER NOT NULL
);
""",
    # 2 -> 3 (ADR-0021): which application a run analysed and how that id was obtained. Runs
    # recorded earlier keep NULL ("unrecorded"); nothing is back-filled.
    2: """
ALTER TABLE runs ADD COLUMN app_id TEXT;
ALTER TABLE runs ADD COLUMN app_id_how TEXT;
""",
    # 3 -> 4 (ADR-0023): the retrieval profile a run's context was assembled with, kept beside the
    # pack so it can be queried and cross-checked. Older rows keep NULL = read as `precision`.
    3: """
ALTER TABLE run_context ADD COLUMN profile TEXT;
""",
    # 4 -> 5 (ADR-0038): one row per model call, keyed to the attempt that made it. Runs recorded
    # earlier have none; nothing is back-filled. Token columns are NULL when the provider did not
    # report them (unknown, never 0).
    4: """
CREATE TABLE model_calls (
    run_id TEXT NOT NULL,
    agent_task_id TEXT NOT NULL,
    call_no INTEGER NOT NULL CHECK (call_no >= 1),
    step_id TEXT NOT NULL,
    entry_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    task_type TEXT NOT NULL,
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    result TEXT NOT NULL,
    PRIMARY KEY (run_id, agent_task_id, call_no),
    FOREIGN KEY (run_id, step_id) REFERENCES steps(run_id, step_id)
);
""",
    # 5 -> 6 (ADR-0039): decision records, their citations and their commit/branch/PR links. All
    # three tables are append-only (a correction is a new decision that supersedes the old one).
    5: """
CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    app_id TEXT,
    fingerprint TEXT,
    run_id TEXT,
    step_id TEXT,
    made_by TEXT NOT NULL CHECK (made_by IN ('person', 'model')),
    recorder TEXT,
    entry_id TEXT,
    provider TEXT,
    model TEXT,
    role TEXT,
    agent_task_id TEXT,
    basis TEXT NOT NULL CHECK (basis IN ('person', 'model_unverified')),
    summary TEXT NOT NULL,
    options_json TEXT NOT NULL,
    chosen TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    supersedes TEXT REFERENCES decisions(decision_id),
    created_at TEXT NOT NULL,
    redactions_json TEXT NOT NULL,
    CHECK ((made_by = 'person') = (basis = 'person'))
);
CREATE TABLE decision_citations (
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    n INTEGER NOT NULL CHECK (n >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('node', 'file', 'decision')),
    target TEXT NOT NULL,
    sha256 TEXT,
    PRIMARY KEY (decision_id, n)
);
CREATE TABLE decision_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    kind TEXT NOT NULL CHECK (kind IN ('commit', 'branch', 'pr')),
    value TEXT NOT NULL,
    how TEXT NOT NULL CHECK (how IN ('observed', 'declared')),
    recorder TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX decision_links_by_value ON decision_links (kind, value);
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decision_citations_no_update BEFORE UPDATE ON decision_citations
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decision_citations_no_delete BEFORE DELETE ON decision_citations
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decision_links_no_update BEFORE UPDATE ON decision_links
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decision_links_no_delete BEFORE DELETE ON decision_links
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
""",
    # 6 -> 7 (ADR-0040): a model call may belong to the run instead of a plan step (the conductor's
    # own draft calls). SQLite cannot drop NOT NULL, so the table is rebuilt; rows are copied as is.
    # The composite foreign key is not enforced for a NULL step_id.
    6: """
CREATE TABLE model_calls_v7 (
    run_id TEXT NOT NULL,
    agent_task_id TEXT NOT NULL,
    call_no INTEGER NOT NULL CHECK (call_no >= 1),
    step_id TEXT,
    entry_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    task_type TEXT NOT NULL,
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    result TEXT NOT NULL,
    PRIMARY KEY (run_id, agent_task_id, call_no),
    FOREIGN KEY (run_id, step_id) REFERENCES steps(run_id, step_id)
);
INSERT INTO model_calls_v7 SELECT run_id, agent_task_id, call_no, step_id, entry_id, provider,
    model, role, task_type, latency_ms, prompt_tokens, completion_tokens, result FROM model_calls;
DROP TABLE model_calls;
ALTER TABLE model_calls_v7 RENAME TO model_calls;
""",
    # 7 -> 8 (ADR-0042): changes made by the small-change agent and their append-only events. The
    # status of a change is derived from the events; nothing here is ever updated or deleted.
    7: """
CREATE TABLE changes (
    change_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    app_id TEXT,
    base_branch TEXT,
    base_commit TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree TEXT NOT NULL,
    max_files INTEGER NOT NULL CHECK (max_files >= 1),
    max_lines INTEGER NOT NULL CHECK (max_lines >= 1),
    request_len INTEGER NOT NULL CHECK (request_len >= 0),
    created_at TEXT NOT NULL
);
CREATE TABLE change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL REFERENCES changes(change_id),
    kind TEXT NOT NULL CHECK (kind IN ('applied', 'refused', 'impact', 'check', 'check_skipped',
        'approved', 'pushed', 'push_failed', 'pr_opened', 'pr_failed', 'discarded')),
    commit_sha TEXT,
    diff_sha256 TEXT,
    ref TEXT,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX change_events_by_change ON change_events (change_id, id);
CREATE TRIGGER changes_no_update BEFORE UPDATE ON changes
BEGIN SELECT RAISE(ABORT, 'changes are append-only'); END;
CREATE TRIGGER changes_no_delete BEFORE DELETE ON changes
BEGIN SELECT RAISE(ABORT, 'changes are append-only'); END;
CREATE TRIGGER change_events_no_update BEFORE UPDATE ON change_events
BEGIN SELECT RAISE(ABORT, 'changes are append-only'); END;
CREATE TRIGGER change_events_no_delete BEFORE DELETE ON change_events
BEGIN SELECT RAISE(ABORT, 'changes are append-only'); END;
""",
}
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
                version = _create_baseline(conn)
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
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO runs (run_id, task_id, state, status, started_at, finished_at,"
                " plan_json, source_root, source_fingerprint, env_json, redactions_json,"
                " app_id, app_id_how)"
                " VALUES (?, ?, 'running', NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    header.run_id,
                    header.task_id,
                    header.started_at.isoformat(),
                    json.dumps(header.plan),
                    header.source_root,
                    header.source_fingerprint,
                    header.environment.model_dump_json(),
                    json.dumps(list(header.redactions)),
                    header.app_id,
                    header.app_id_how.value if header.app_id_how is not None else None,
                ),
            )
            if header.recalled is not None and header.request_length is not None:
                conn.execute(
                    "INSERT INTO run_context (run_id, context_json, request_len, profile)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        header.run_id,
                        header.recalled.model_dump_json(),
                        header.request_length,
                        header.recalled.profile,
                    ),
                )
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot record the run (sqlite error)") from None

    def finish_run(
        self,
        run_id: str,
        status: TraceStatus,
        finished_at: datetime,
        redactions: Sequence[str],
        steps: Sequence[StepTrace],
        calls: Sequence[CallRecord] = (),
        decisions: Sequence[Decision] = (),
    ) -> None:
        """Write steps, attempts, calls, decisions and the final status in one transaction, or
        nothing. Decisions (ADR-0040: the conductor's) are checked after the calls are written, so
        a decision may name a call of this run; they go in order, so a later one may cite an
        earlier one."""
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
            step_ids = {step.step_id for step in steps}
            for call in calls:
                if (
                    call.step_id is not None and call.step_id not in step_ids
                ):  # cannot happen from the orchestrator; never lose the run
                    continue
                conn.execute(
                    "INSERT INTO model_calls (run_id, agent_task_id, call_no, step_id, entry_id,"
                    " provider, model, role, task_type, latency_ms, prompt_tokens,"
                    " completion_tokens, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        call.agent_task_id,
                        call.call_no,
                        call.step_id,
                        call.entry_id,
                        call.provider,
                        call.model,
                        call.role,
                        call.task_type,
                        call.latency_ms,
                        call.prompt_tokens,
                        call.completion_tokens,
                        call.result.value,
                    ),
                )
            for decision in decisions:
                self._insert_decision(decision)
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError(
                "cannot finish the run (sqlite error); nothing was written"
            ) from None
        except (TraceStoreError, DecisionError):
            _rollback(conn)
            raise

    def add_decision(self, decision: Decision) -> None:
        """Append one decision with its citations (ADR-0039), or nothing.

        Refuses a superseded or cited decision that does not exist or belongs to another
        application, a run or step that does not exist, and a model call that does not exist.
        """
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_decision(decision)
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            _rollback(conn)
            raise DecisionError(
                "the decision is already recorded or refers to a missing row"
            ) from None
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot record the decision (sqlite error)") from None
        except DecisionError:
            _rollback(conn)
            raise

    def _insert_decision(self, decision: Decision) -> None:
        """The writes of `add_decision`, inside a transaction the caller owns."""
        conn = self._conn
        self._check_decision_refs(decision)
        if conn.execute(
            "SELECT 1 FROM decisions WHERE decision_id = ?", (decision.decision_id,)
        ).fetchone():
            raise DuplicateDecisionError("the decision is already recorded")
        conn.execute(
            "INSERT INTO decisions (decision_id, app_id, fingerprint, run_id, step_id,"
            " made_by, recorder, entry_id, provider, model, role, agent_task_id, basis,"
            " summary, options_json, chosen, reasoning, supersedes, created_at,"
            " redactions_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?)",
            (
                decision.decision_id,
                decision.app_id,
                decision.fingerprint,
                decision.run_id,
                decision.step_id,
                decision.made_by.value,
                decision.recorder,
                decision.entry_id,
                decision.provider,
                decision.model,
                decision.role,
                decision.agent_task_id,
                decision.basis.value,
                decision.summary,
                json.dumps(list(decision.options)),
                decision.chosen,
                decision.reasoning,
                decision.supersedes,
                decision.created_at.isoformat(),
                json.dumps(list(decision.redactions)),
            ),
        )
        for n, cite in enumerate(decision.citations, start=1):
            conn.execute(
                "INSERT INTO decision_citations (decision_id, n, kind, target, sha256)"
                " VALUES (?, ?, ?, ?, ?)",
                (decision.decision_id, n, cite.kind.value, cite.target, cite.sha256),
            )

    def _check_decision_refs(self, decision: Decision) -> None:
        conn = self._conn
        ids = [decision.supersedes] if decision.supersedes else []
        ids += [c.target for c in decision.citations if c.kind is CitationKind.DECISION]
        for other_id in ids:
            row = conn.execute(
                "SELECT app_id FROM decisions WHERE decision_id = ?", (other_id,)
            ).fetchone()
            if row is None:
                raise DecisionError("a cited or superseded decision does not exist")
            if row["app_id"] != decision.app_id:
                raise DecisionError("a cited or superseded decision belongs to another application")
        if decision.run_id is not None and decision.step_id is None:  # ADR-0040: a run-level one
            found = conn.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (decision.run_id,)
            ).fetchone()
            if found is None:
                raise DecisionError("the run of the decision does not exist")
        elif decision.run_id is not None:
            found = conn.execute(
                "SELECT 1 FROM steps WHERE run_id = ? AND step_id = ?",
                (decision.run_id, decision.step_id),
            ).fetchone()
            if found is None:
                raise DecisionError("the run or step of the decision does not exist")
        if decision.agent_task_id is not None:
            found = conn.execute(
                "SELECT 1 FROM model_calls WHERE agent_task_id = ? AND (? IS NULL OR run_id = ?)",
                (decision.agent_task_id, decision.run_id, decision.run_id),
            ).fetchone()
            if found is None:
                raise DecisionError("the model call of the decision was not recorded")

    def add_decision_link(self, link: DecisionLink) -> None:
        """Append one commit, branch or PR link to an existing decision."""
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            found = conn.execute(
                "SELECT 1 FROM decisions WHERE decision_id = ?", (link.decision_id,)
            ).fetchone()
            if found is None:
                raise DecisionError("no decision has this id")
            conn.execute(
                "INSERT INTO decision_links (decision_id, kind, value, how, recorder, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    link.decision_id,
                    link.kind.value,
                    link.value,
                    link.how.value,
                    link.recorder,
                    link.created_at.isoformat(),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot record the link (sqlite error)") from None
        except DecisionError:
            _rollback(conn)
            raise

    # -- reading ---------------------------------------------------------------------------

    def read_decisions(self) -> DecisionListing:
        """Every decision (oldest first) and link; a malformed row is reported by id."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM decisions ORDER BY created_at, decision_id"
            ).fetchall()
            cites = self._conn.execute(
                "SELECT * FROM decision_citations ORDER BY decision_id, n"
            ).fetchall()
            link_rows = self._conn.execute("SELECT * FROM decision_links ORDER BY id").fetchall()
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        by_decision: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for cite in cites:
            by_decision[str(cite["decision_id"])].append(cite)
        decisions: list[Decision] = []
        malformed: list[str] = []
        for row in rows:
            decision_id = str(row["decision_id"])
            try:
                decisions.append(_build_decision(row, by_decision.get(decision_id, [])))
            except (ValueError, TypeError, DecisionError):  # ValidationError is a ValueError
                malformed.append(decision_id)
        known = {d.decision_id: d for d in decisions}
        for d in decisions:  # a reference that points nowhere is malformed, never silently kept
            refs = [c.target for c in d.citations if c.kind is CitationKind.DECISION]
            if d.supersedes:
                refs.append(d.supersedes)
            if any(ref not in known for ref in refs) and d.decision_id not in malformed:
                malformed.append(d.decision_id)
        good = tuple(d for d in decisions if d.decision_id not in malformed)
        links: list[DecisionLink] = []
        for link_row in link_rows:
            try:
                links.append(
                    DecisionLink(
                        id=link_row["id"],
                        decision_id=link_row["decision_id"],
                        kind=link_row["kind"],
                        value=link_row["value"],
                        how=link_row["how"],
                        recorder=link_row["recorder"],
                        created_at=link_row["created_at"],
                    )
                )
            except (ValueError, DecisionError):
                malformed.append(f"link:{link_row['id']}")
        return DecisionListing(
            decisions=good,
            links=tuple(link for link in links if link.decision_id in known),
            malformed=tuple(sorted(set(malformed))),
        )

    # -- changes (ADR-0042) ----------------------------------------------------------------

    def add_change(self, change: ChangeRecord) -> None:
        """Record the header of a change. Written once; events follow."""
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO changes (change_id, project, app_id, base_branch, base_commit,"
                " branch, worktree, max_files, max_lines, request_len, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    change.change_id,
                    change.project,
                    change.app_id,
                    change.base_branch,
                    change.base_commit,
                    change.branch,
                    change.worktree,
                    change.max_files,
                    change.max_lines,
                    change.request_len,
                    _iso(change.created_at),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            _rollback(conn)
            raise TraceStoreError("that change is already recorded") from None
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot record the change (sqlite error)") from None

    def add_change_event(self, event: ChangeEvent) -> int:
        """Append one event to a recorded change; returns its number."""
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO change_events (change_id, kind, commit_sha, diff_sha256, ref, detail,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.change_id,
                    event.kind.value,
                    event.commit,
                    event.diff_sha256,
                    event.ref,
                    event.detail,
                    _iso(event.created_at),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            _rollback(conn)
            raise TraceStoreError(
                "the change event refers to a change that is not recorded"
            ) from None
        except sqlite3.Error:
            _rollback(conn)
            raise TraceStoreError("cannot record the change event (sqlite error)") from None
        return int(cur.lastrowid or 0)

    def read_changes(self) -> list[tuple[ChangeRecord, list[ChangeEvent]]]:
        """Every change (oldest first) with its events in order; a malformed row is an error."""
        try:
            heads = self._conn.execute(
                "SELECT * FROM changes ORDER BY created_at, change_id"
            ).fetchall()
            rows = self._conn.execute("SELECT * FROM change_events ORDER BY id").fetchall()
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        try:
            events: dict[str, list[ChangeEvent]] = defaultdict(list)
            for row in rows:
                events[str(row["change_id"])].append(
                    ChangeEvent(
                        id=row["id"],
                        change_id=row["change_id"],
                        kind=row["kind"],
                        commit=row["commit_sha"],
                        diff_sha256=row["diff_sha256"],
                        ref=row["ref"],
                        detail=row["detail"],
                        created_at=row["created_at"],
                    )
                )
            return [
                (
                    ChangeRecord(
                        change_id=h["change_id"],
                        project=h["project"],
                        app_id=h["app_id"],
                        base_branch=h["base_branch"],
                        base_commit=h["base_commit"],
                        branch=h["branch"],
                        worktree=h["worktree"],
                        max_files=h["max_files"],
                        max_lines=h["max_lines"],
                        request_len=h["request_len"],
                        created_at=h["created_at"],
                    ),
                    events.get(str(h["change_id"]), []),
                )
                for h in heads
            ]
        except ValueError:
            raise TraceStoreError("the trace database holds a malformed change row") from None

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
            context = self._conn.execute(
                "SELECT * FROM run_context WHERE run_id = ?", (run_id,)
            ).fetchone()
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        return _build_run(row, steps, attempts, context)

    def call_rows(self) -> CallListing:
        """Every recorded model call with the status of its step and attempt (ADR-0038).

        A malformed row, or one that contradicts its run, is reported by run id, not dropped.
        """
        try:
            rows = self._conn.execute(
                "SELECT c.*, r.task_id AS task_id, s.status AS step_status,"
                " a.result_status AS attempt_status"
                " FROM model_calls c"
                " JOIN runs r ON r.run_id = c.run_id"
                " LEFT JOIN steps s ON s.run_id = c.run_id AND s.step_id = c.step_id"
                " LEFT JOIN attempts a ON a.run_id = c.run_id AND a.step_id = c.step_id"
                " AND a.agent_task_id = c.agent_task_id"
                " ORDER BY c.run_id, c.agent_task_id, c.call_no"
            ).fetchall()
            runs_total = int(self._conn.execute("SELECT count(*) FROM runs").fetchone()[0])
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        parsed: list[CallRow] = []
        malformed: set[str] = set()
        for row in rows:
            run_id = str(row["run_id"])
            try:
                record = CallRecord.model_validate(
                    {name: row[name] for name in CallRecord.model_fields}
                )
                owner = record.step_id if record.step_id is not None else "_conductor"
                if not record.agent_task_id.startswith(f"{row['task_id']}.{owner}."):
                    raise ValueError("the call does not belong to its step")
                if (record.step_id is None) != (row["step_status"] is None):
                    raise ValueError("the call and its step disagree")
                attempt = row["attempt_status"]
                if attempt not in (None, "success", "failure"):
                    raise ValueError("the attempt status is unknown")
                parsed.append(
                    CallRow(
                        run_id=run_id,
                        call=record,
                        step_status=_step_status(row["step_status"]),
                        attempt_status=attempt,
                    )
                )
            except ValueError:  # pydantic's ValidationError is a ValueError
                malformed.add(run_id)
        with_calls = len({row.run_id for row in parsed} | malformed)
        return CallListing(
            rows=tuple(parsed),
            malformed_runs=tuple(sorted(malformed)),
            runs_total=runs_total,
            runs_with_calls=with_calls,
        )

    def iter_runs(self) -> Iterator[tuple[str, RunTrace | None]]:
        """Every run once, newest first, as (run id, trace). An unreadable run gives (id, None).

        One pass over the database, so callers that need the traces do not read each run twice.
        """
        try:
            ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT run_id FROM runs ORDER BY started_at DESC, run_id"
                )
            ]
        except sqlite3.Error:
            raise TraceStoreError("cannot read the trace database (sqlite error)") from None
        for run_id in ids:
            try:
                yield run_id, self.get(run_id)
            except TraceRecordError:
                yield run_id, None

    def list(self) -> TraceListing:
        """All readable runs, newest first; unreadable ones are reported, never hidden."""
        runs: list[RunSummary] = []
        malformed: list[str] = []
        for run_id, trace in self.iter_runs():
            if trace is None:
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


def _build_decision(row: sqlite3.Row, cites: Sequence[sqlite3.Row]) -> Decision:
    """Rebuild and validate one decision. Raises ValueError/DecisionError on a malformed row."""
    options = json.loads(row["options_json"])
    redactions = json.loads(row["redactions_json"])
    if not (isinstance(options, list) and isinstance(redactions, list)):
        raise ValueError("malformed json column")
    citations = []
    for number, cite in enumerate(cites, start=1):
        if cite["n"] != number:
            raise ValueError("citations are not numbered 1..n")
        citations.append(Citation(kind=cite["kind"], target=cite["target"], sha256=cite["sha256"]))
    decision = Decision(
        decision_id=row["decision_id"],
        app_id=row["app_id"],
        fingerprint=row["fingerprint"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        made_by=row["made_by"],
        recorder=row["recorder"],
        entry_id=row["entry_id"],
        provider=row["provider"],
        model=row["model"],
        role=row["role"],
        agent_task_id=row["agent_task_id"],
        basis=row["basis"],
        summary=row["summary"],
        options=tuple(options),
        chosen=row["chosen"],
        reasoning=row["reasoning"],
        citations=tuple(citations),
        supersedes=row["supersedes"],
        created_at=row["created_at"],
        redactions=tuple(redactions),
    )
    if decision_digest(decision) != decision.decision_id:
        raise DecisionError("the stored decision does not match its id")
    return decision


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
            conn.execute("BEGIN IMMEDIATE")
            # The version is re-read under the write lock: a script that happens to succeed twice
            # (the 6 to 7 rebuild does) must not run again and set the version back (ADR-0042).
            held = _user_version(conn)
            if held != version:
                _rollback(conn)
                version = held
                continue
            for statement in _statements(script):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except sqlite3.Error:
            _rollback(conn)
            # Another process may have applied this step between our version read and our write.
            current = _current_version(conn)
            if current <= version:
                raise TraceStoreError("cannot upgrade the trace database (sqlite error)") from None
            version = current
            continue
        version += 1


def _statements(script: str) -> Iterator[str]:
    """The statements of a migration script, one by one (a trigger body keeps its semicolons)."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending.strip()
            pending = ""


def _create_baseline(conn: sqlite3.Connection) -> int:
    """Create the baseline schema in an empty file; return the version the file now has.

    Another process can create (and migrate) the same new file between our version read and our
    write, so a failure, or tables that appeared meanwhile, is settled by reading the version again.
    A file that holds tables but still has no version is not ours and is refused untouched.
    """
    try:
        if not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
            conn.executescript(_SCHEMA)
            return _BASELINE_VERSION
    except sqlite3.Error:
        _rollback(conn)
        current = _current_version(conn)
        if current == 0:
            raise  # a real failure, not a lost race
        return current
    current = _current_version(conn)
    if current == 0:
        raise TraceStoreError("the file is a database but not a trace database")
    return current


def _current_version(conn: sqlite3.Connection) -> int:
    """The version now on disk, or 0 when it cannot be read."""
    try:
        return _user_version(conn)
    except sqlite3.Error:
        return 0


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _step_status(value: str | None) -> StepStatus | None:
    """A call row of the conductor has no step, hence no step status."""
    return StepStatus(value) if value is not None else None


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        conn.execute("ROLLBACK")


def _build_run(
    row: sqlite3.Row,
    step_rows: Sequence[sqlite3.Row],
    attempt_rows: Sequence[sqlite3.Row],
    context_row: sqlite3.Row | None = None,
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
            app_id=row["app_id"],
            app_id_how=row["app_id_how"],
            environment=environment,
            redactions=tuple(str(r) for r in redactions),
            plan=plan,
            steps=steps,
            recalled=(
                RecalledContext.model_validate_json(context_row["context_json"])
                if context_row is not None
                else None
            ),
            request_length=context_row["request_len"] if context_row is not None else None,
        )
        if (
            context_row is not None
            and context_row["profile"] is not None
            and trace.recalled is not None
            and context_row["profile"] != trace.recalled.profile
        ):
            raise bad("the stored context profile disagrees with the stored pack")
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
