"""The workspace file (ADR-0036). The only module that imports `sqlite3` for it.

Removing a project sets `removed_at`; its row and the links that named it stay. A foreign file, a
newer version or malformed rows are refused. Free text is checked and redacted before storing.
"""

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

from pydantic import ValidationError

from cognitive_mesh.identity import IdentityError, validate_app_id
from cognitive_mesh.ingestion.secrets import redact_secrets
from cognitive_mesh.workspace.models import (
    CheckCommand,
    DeclaredLink,
    Project,
    ProjectKind,
    WorkspaceError,
)

SCHEMA_VERSION: Final = 2
APPLICATION_ID: Final = 0x434D5753  # "CMWS": tells a workspace file from any other SQLite file
MAX_NOTE_CHARS: Final = 1000
MAX_BY_CHARS: Final = 100
MAX_KIND_CHARS: Final = 40
MAX_CHECK_ARGS: Final = 50
MAX_CHECK_ARG_CHARS: Final = 500
DEFAULT_CHECK_TIMEOUT_S: Final = 300
MAX_CHECK_TIMEOUT_S: Final = 3600

_CHECKS_TABLE: Final = """
CREATE TABLE checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    argv_json TEXT NOT NULL,
    timeout_s INTEGER NOT NULL,
    by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_SCHEMA: Final = f"""
PRAGMA application_id = {APPLICATION_ID};
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('local', 'github')),
    location TEXT NOT NULL,
    commit_sha TEXT,
    app_id TEXT,
    app_id_how TEXT,
    added_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE UNIQUE INDEX projects_active_name ON projects(name) WHERE removed_at IS NULL;
CREATE TABLE declared_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_project INTEGER NOT NULL REFERENCES projects(id),
    target_project INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    note TEXT NOT NULL,
    by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
{_CHECKS_TABLE}
PRAGMA user_version = {SCHEMA_VERSION};
"""


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _clean(text: str, limit: int, what: str) -> str:
    if not text.strip():
        raise WorkspaceError(f"the {what} is empty")
    if len(text) > limit:
        raise WorkspaceError(f"the {what} is longer than {limit} characters")
    if not text.isprintable():
        raise WorkspaceError(f"the {what} contains control characters")
    redacted, _ = redact_secrets(text.strip())
    return redacted


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def _project(row: tuple[Any, ...]) -> Project:
    try:
        return Project(
            id=row[0],
            name=row[1],
            kind=ProjectKind(row[2]),
            location=row[3],
            commit=row[4],
            app_id=row[5],
            app_id_how=row[6],
            added_at=datetime.fromisoformat(str(row[7])),
            removed_at=datetime.fromisoformat(str(row[8])) if row[8] else None,
        )
    except (ValueError, ValidationError):
        raise WorkspaceError("the workspace file holds a malformed project row") from None


_PROJECT_COLUMNS = "id, name, kind, location, commit_sha, app_id, app_id_how, added_at, removed_at"


class WorkspaceStore:
    def __init__(self, conn: sqlite3.Connection, *, writable: bool) -> None:
        self._conn = conn
        self._writable = writable

    @classmethod
    def create(cls, path: Path) -> Self:
        """Open for writing; a missing file is created, any other file must be a workspace file."""
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error:
            raise WorkspaceError("cannot open the workspace file (sqlite error)") from None
        try:
            fresh = (
                _pragma_int(conn, "application_id") == 0
                and not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            )
            if fresh:
                conn.executescript(f"BEGIN IMMEDIATE; {_SCHEMA} COMMIT;")
            else:
                cls._check_header(conn)
                if _pragma_int(conn, "user_version") == 1:
                    conn.executescript(
                        f"BEGIN IMMEDIATE; {_CHECKS_TABLE} PRAGMA user_version = {SCHEMA_VERSION};"
                        " COMMIT;"
                    )
        except sqlite3.Error:
            conn.close()
            raise WorkspaceError("cannot open the workspace file (sqlite error)") from None
        except WorkspaceError:
            conn.close()
            raise
        return cls(conn, writable=True)

    @classmethod
    def open_existing(cls, path: Path) -> Self:
        """Open read-only; never creates a file."""
        if not path.is_file():
            raise WorkspaceError("the workspace file does not exist")
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
            conn.execute("PRAGMA query_only = ON")
            cls._check_header(conn)
        except sqlite3.Error:
            raise WorkspaceError("cannot open the workspace file (sqlite error)") from None
        except WorkspaceError:
            conn.close()
            raise
        return cls(conn, writable=False)

    @staticmethod
    def _check_header(conn: sqlite3.Connection) -> None:
        if _pragma_int(conn, "application_id") != APPLICATION_ID:
            raise WorkspaceError("the file is not a workspace file")
        version = _pragma_int(conn, "user_version")
        if version > SCHEMA_VERSION:
            raise WorkspaceError(
                f"the workspace file has version {version}, newer than this program supports"
            )
        if version not in (1, SCHEMA_VERSION):
            raise WorkspaceError(f"unsupported workspace file version ({version})")

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
            raise WorkspaceError("the workspace file was opened read-only")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(sql, params)
                self._conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("ROLLBACK")
                raise
        except sqlite3.IntegrityError:
            raise WorkspaceError("a project with that name is already in the workspace") from None
        except sqlite3.Error:
            raise WorkspaceError("cannot write the workspace file (sqlite error)") from None
        return int(cur.rowcount if sql.startswith("UPDATE") else (cur.lastrowid or 0))

    def add_project(
        self,
        name: str,
        kind: ProjectKind,
        location: str,
        *,
        commit: str | None = None,
        app_id: str | None = None,
        app_id_how: str | None = None,
        now: datetime | None = None,
    ) -> Project:
        try:
            validate_app_id(name)
        except IdentityError as exc:
            raise WorkspaceError(f"the project name is not acceptable: {exc}") from None
        if kind is ProjectKind.GITHUB and commit is None:
            raise WorkspaceError("a github project needs a recorded commit")
        added = _stamp(now)
        new_id = self._write(
            "INSERT INTO projects (name, kind, location, commit_sha, app_id, app_id_how,"
            " added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, kind.value, location, commit, app_id, app_id_how, added),
        )
        return Project(
            id=new_id,
            name=name,
            kind=kind,
            location=location,
            commit=commit,
            app_id=app_id,
            app_id_how=app_id_how,
            added_at=datetime.fromisoformat(added),
        )

    def projects(self, *, include_removed: bool = False) -> list[Project]:
        where = "" if include_removed else " WHERE removed_at IS NULL"
        try:
            rows = self._conn.execute(
                f"SELECT {_PROJECT_COLUMNS} FROM projects{where} ORDER BY id"  # noqa: S608
            ).fetchall()
        except sqlite3.Error:
            raise WorkspaceError("cannot read the workspace file (sqlite error)") from None
        return [_project(r) for r in rows]

    def project(self, name: str) -> Project:
        for item in self.projects():
            if item.name == name:
                return item
        raise WorkspaceError("no project in the workspace has that name")

    def remove_project(self, name: str, *, now: datetime | None = None) -> None:
        """Soft remove: the row and the links stay, and stop being listed or computed."""
        found = self.project(name)
        self._write(
            "UPDATE projects SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
            (_stamp(now), found.id),
        )

    def set_commit(self, name: str, commit: str) -> None:
        """Move a github project's pin. Only an explicit command calls this."""
        found = self.project(name)
        if found.kind is not ProjectKind.GITHUB:
            raise WorkspaceError("only a github project has a recorded commit")
        self._write("UPDATE projects SET commit_sha = ? WHERE id = ?", (commit, found.id))

    def add_link(
        self,
        source: str,
        target: str,
        kind: str,
        note: str,
        by: str,
        *,
        now: datetime | None = None,
    ) -> DeclaredLink:
        if source == target:
            raise WorkspaceError("a project cannot be linked to itself")
        src, dst = self.project(source), self.project(target)
        clean_kind = _clean(kind, MAX_KIND_CHARS, "link kind")
        clean_note = _clean(note, MAX_NOTE_CHARS, "link note")
        clean_by = _clean(by, MAX_BY_CHARS, "author")
        created = _stamp(now)
        new_id = self._write(
            "INSERT INTO declared_links (source_project, target_project, kind, note, by,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (src.id, dst.id, clean_kind, clean_note, clean_by, created),
        )
        return DeclaredLink(
            id=new_id,
            source=source,
            target=target,
            kind=clean_kind,
            note=clean_note,
            by=clean_by,
            created_at=datetime.fromisoformat(created),
        )

    def links(self) -> list[DeclaredLink]:
        """Links whose two projects are both still in the workspace."""
        try:
            rows = self._conn.execute(
                "SELECT l.id, s.name, t.name, l.kind, l.note, l.by, l.created_at"
                " FROM declared_links l JOIN projects s ON s.id = l.source_project"
                " JOIN projects t ON t.id = l.target_project"
                " WHERE s.removed_at IS NULL AND t.removed_at IS NULL ORDER BY l.id"
            ).fetchall()
            return [
                DeclaredLink(
                    id=r[0],
                    source=r[1],
                    target=r[2],
                    kind=r[3],
                    note=r[4],
                    by=r[5],
                    created_at=datetime.fromisoformat(r[6]),
                )
                for r in rows
            ]
        except sqlite3.Error:
            raise WorkspaceError("cannot read the workspace file (sqlite error)") from None
        except ValueError:
            raise WorkspaceError("the workspace file holds a malformed link row") from None

    def set_check(
        self,
        name: str,
        argv: Sequence[str],
        *,
        timeout_s: int = DEFAULT_CHECK_TIMEOUT_S,
        by: str,
        now: datetime | None = None,
    ) -> CheckCommand:
        """Store the command a person declares as the project's check (ADR-0042). History stays."""
        found = self.project(name)
        if _pragma_int(self._conn, "user_version") < SCHEMA_VERSION:
            raise WorkspaceError("the workspace file is an older version")
        words = list(argv)
        if not words or len(words) > MAX_CHECK_ARGS:
            raise WorkspaceError(f"a check command needs 1 to {MAX_CHECK_ARGS} words")
        for word in words:
            if not word or len(word) > MAX_CHECK_ARG_CHARS or not word.isprintable():
                raise WorkspaceError("a check command word is empty, too long or not printable")
        if not 1 <= timeout_s <= MAX_CHECK_TIMEOUT_S:
            raise WorkspaceError(f"the check timeout must be 1 to {MAX_CHECK_TIMEOUT_S} seconds")
        clean_by = _clean(by, MAX_BY_CHARS, "author")
        created = _stamp(now)
        new_id = self._write(
            "INSERT INTO checks (project_id, argv_json, timeout_s, by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (found.id, json.dumps(words), timeout_s, clean_by, created),
        )
        return CheckCommand(
            id=new_id,
            project=name,
            argv=tuple(words),
            timeout_s=timeout_s,
            by=clean_by,
            created_at=datetime.fromisoformat(created),
        )

    def check_command(self, name: str) -> CheckCommand | None:
        """The newest declared check of a project, or None (also for a version 1 file)."""
        found = self.project(name)
        if _pragma_int(self._conn, "user_version") < SCHEMA_VERSION:
            return None
        try:
            row = self._conn.execute(
                "SELECT id, argv_json, timeout_s, by, created_at FROM checks"
                " WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (found.id,),
            ).fetchone()
            if row is None:
                return None
            words = json.loads(row[1])
            if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
                raise ValueError("argv")
            return CheckCommand(
                id=row[0],
                project=name,
                argv=tuple(words),
                timeout_s=row[2],
                by=row[3],
                created_at=datetime.fromisoformat(row[4]),
            )
        except sqlite3.Error:
            raise WorkspaceError("cannot read the workspace file (sqlite error)") from None
        except (ValueError, ValidationError):
            raise WorkspaceError("the workspace file holds a malformed check row") from None
