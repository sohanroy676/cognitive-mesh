"""The experience graph file (ADR-0015). The only module that imports `sqlite3` for the graph.

The file is a derived copy of the trace database. It is rewritten whole in one transaction, and it
is only trusted on load after its content hash, its schema and its edges all check out.
"""

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from cognitive_mesh.experience.models import (
    FORMAT_VERSION,
    ExpEdge,
    ExperienceDiagnostic,
    ExperienceGraphError,
    ExpNode,
    Snapshot,
    TraceRef,
    content_sha256,
    ref_key,
)

SCHEMA_VERSION: Final = 1
APPLICATION_ID: Final = 0x434D4547  # "CMEG": tells a graph file from any other SQLite file

_SCHEMA: Final = f"""
PRAGMA application_id = {APPLICATION_ID};
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE nodes (id TEXT PRIMARY KEY, node_json TEXT NOT NULL);
CREATE TABLE edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (source, target, kind)
);
CREATE TABLE edge_refs (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref_json TEXT NOT NULL,
    PRIMARY KEY (source, target, kind, ref_json),
    FOREIGN KEY (source, target, kind) REFERENCES edges (source, target, kind) ON DELETE CASCADE
);
CREATE TABLE diagnostics (seq INTEGER PRIMARY KEY, diagnostic_json TEXT NOT NULL);
PRAGMA user_version = {SCHEMA_VERSION};
"""


@dataclass(frozen=True)
class LoadedGraph:
    snapshot: Snapshot
    built_at: datetime
    content_sha256: str


def check_consistent(snapshot: Snapshot) -> None:
    """Refuse a snapshot whose edges point at missing nodes, or that repeats a node or an edge."""
    ids = [n.id for n in snapshot.nodes]
    if len(set(ids)) != len(ids):
        raise ExperienceGraphError("the experience graph holds a duplicate node")
    known = set(ids)
    keys = [(e.source, e.target, e.kind) for e in snapshot.edges]
    if len(set(keys)) != len(keys):
        raise ExperienceGraphError("the experience graph holds a duplicate edge")
    if any(e.source not in known or e.target not in known for e in snapshot.edges):
        raise ExperienceGraphError("the experience graph holds an edge whose endpoint is missing")


def _connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(str(path), isolation_level=None)
    except sqlite3.Error:
        raise ExperienceGraphError("cannot open the experience graph (sqlite error)") from None
    return conn


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _is_fresh(conn: sqlite3.Connection) -> bool:
    """True for a new, empty file: no application id and no tables."""
    return (
        _pragma_int(conn, "application_id") == 0
        and not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    )


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create the schema in one transaction, so no process ever sees a half-made file.

    If this fails but the file is by now a valid graph file, another process created it first
    (several `experience build` calls at once on a new path): that file is used. Anything else
    is raised as it was.
    """
    try:
        conn.executescript(f"BEGIN IMMEDIATE; {_SCHEMA} COMMIT;")
    except sqlite3.Error:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        try:
            _check_header(conn)
        except (ExperienceGraphError, sqlite3.Error):
            raise sqlite3.Error("schema") from None
        return


def _check_header(conn: sqlite3.Connection) -> None:
    if _pragma_int(conn, "application_id") != APPLICATION_ID:
        raise ExperienceGraphError("the file is not an experience graph file")
    version = _pragma_int(conn, "user_version")
    if version > SCHEMA_VERSION:
        raise ExperienceGraphError(
            f"the experience graph has version {version}, newer than this program supports"
        )
    if version != SCHEMA_VERSION:
        raise ExperienceGraphError(f"unsupported experience graph version ({version})")


def save(path: Path, snapshot: Snapshot, *, built_at: datetime | None = None) -> str:
    """Write the whole snapshot, replacing any earlier content. Returns the content hash."""
    check_consistent(snapshot)
    stamp = (built_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    digest = content_sha256(snapshot.nodes, snapshot.edges, snapshot.diagnostics)
    conn = _connect(path)
    try:
        if _is_fresh(conn):
            _create_schema(conn)
        else:
            _check_header(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table in ("edge_refs", "edges", "nodes", "diagnostics", "meta"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608  # fixed table names
            conn.executemany(
                "INSERT INTO nodes (id, node_json) VALUES (?, ?)",
                [(n.id, n.model_dump_json()) for n in snapshot.nodes],
            )
            for e in snapshot.edges:
                conn.execute(
                    "INSERT INTO edges (source, target, kind) VALUES (?, ?, ?)",
                    (e.source, e.target, e.kind.value),
                )
                conn.executemany(
                    "INSERT INTO edge_refs (source, target, kind, ref_json) VALUES (?, ?, ?, ?)",
                    [(e.source, e.target, e.kind.value, ref.model_dump_json()) for ref in e.refs],
                )
            conn.executemany(
                "INSERT INTO diagnostics (seq, diagnostic_json) VALUES (?, ?)",
                [(i, d.model_dump_json()) for i, d in enumerate(snapshot.diagnostics)],
            )
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("format_version", str(FORMAT_VERSION)),
                    ("content_sha256", digest),
                    ("built_at", stamp),
                ],
            )
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error:
        raise ExperienceGraphError("cannot write the experience graph (sqlite error)") from None
    finally:
        conn.close()
    return digest


def load(path: Path) -> LoadedGraph:
    """Open read-only and trust nothing: header, records, edges and the content hash are checked."""
    if not path.is_file():
        raise ExperienceGraphError("the experience graph file does not exist")
    conn = _connect(path)
    try:
        conn.execute("PRAGMA query_only = ON")
        _check_header(conn)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        node_rows = conn.execute("SELECT id, node_json FROM nodes ORDER BY id").fetchall()
        edge_rows = conn.execute(
            "SELECT source, target, kind FROM edges ORDER BY source, target, kind"
        ).fetchall()
        ref_rows = conn.execute("SELECT source, target, kind, ref_json FROM edge_refs").fetchall()
        diag_rows = conn.execute("SELECT diagnostic_json FROM diagnostics ORDER BY seq").fetchall()
    except sqlite3.Error:
        raise ExperienceGraphError(
            "cannot read the experience graph (not a SQLite database, or tables are missing)"
        ) from None
    finally:
        conn.close()
    if meta.get("format_version") != str(FORMAT_VERSION):
        raise ExperienceGraphError(
            "the experience graph has an unsupported format version; "
            "rebuild it with `experience build`"
        )
    try:
        nodes = tuple(ExpNode.model_validate_json(row[1]) for row in node_rows)
        if any(node.id != row[0] for node, row in zip(nodes, node_rows, strict=True)):
            raise ExperienceGraphError("the experience graph holds a node under the wrong id")
        refs: dict[tuple[str, str, str], list[TraceRef]] = {}
        for source, target, kind, ref_json in ref_rows:
            refs.setdefault((source, target, kind), []).append(
                TraceRef.model_validate_json(ref_json)
            )
        edges = tuple(
            ExpEdge(
                source=s,
                target=t,
                kind=k,
                refs=tuple(sorted(refs.get((s, t, k), []), key=ref_key)),
            )
            for s, t, k in edge_rows
        )
        diagnostics = tuple(ExperienceDiagnostic.model_validate_json(r[0]) for r in diag_rows)
    except ValidationError:
        raise ExperienceGraphError("the experience graph holds a malformed record") from None
    snapshot = Snapshot(nodes=nodes, edges=edges, diagnostics=diagnostics)
    check_consistent(snapshot)
    digest = content_sha256(nodes, edges, diagnostics)
    if digest != meta.get("content_sha256"):
        raise ExperienceGraphError("the stored content hash does not match the graph content")
    try:
        built_at = datetime.fromisoformat(meta["built_at"])
    except (KeyError, ValueError):
        raise ExperienceGraphError("the experience graph has no valid build time") from None
    return LoadedGraph(snapshot=snapshot, built_at=built_at, content_sha256=digest)
