"""The data server's reads (ADR-0044): the vector index and the SQLite stores, read-only.

Nothing here writes. SQLite files are opened with `mode=ro` and `query_only`; table and column
names come from the file's own schema and are never taken from the request as SQL; the client
sends no SQL. Stored text was redacted when it was written. Long values are cut with a visible
count. The semantic search runs one query at a time (one embedder per process).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from typing import Any, Final

from cognitive_mesh.memory import MemoryIndexError, SourceCheck, verify_source
from cognitive_mesh.serve.config import EmbedderHolder, ServeConfig
from cognitive_mesh.serve.http import ApiError, Handler, Reply, Request, need, need_int
from cognitive_mesh.traces import TraceStore, TraceStoreError

CELL_CHARS: Final = 300
FULL_CHARS: Final = 100_000
MAX_PAGE: Final = 200
MAX_PROJECTION: Final = 5_000
PROJECTION_NOTE: Final = (
    "A 2-D picture of the vectors by principal component analysis (the two directions with the "
    "most spread). Distances in the picture are not distances in the index, and a cluster is not "
    "evidence that two records agree."
)


def _text(value: Any, limit: int = CELL_CHARS) -> tuple[Any, int]:
    """(display value, characters cut). Bytes are never shown."""
    if value is None or isinstance(value, int | float):
        return value, 0
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>", 0
    text = str(value)
    if len(text) > limit:
        return text[:limit], len(text) - limit
    return text, 0


class DataApi:
    def __init__(self, config: ServeConfig) -> None:
        self.config = config
        self.embedder = EmbedderHolder(config.embedder_factory)
        self._search_lock = threading.Lock()

    def routes(self) -> dict[str, Handler]:
        return {
            "/api/data/overview": self.overview,
            "/api/db/tables": self.tables,
            "/api/db/rows": self.rows,
            "/api/db/row": self.row,
            "/api/index/items": self.items,
            "/api/index/item": self.item,
            "/api/index/search": self.search,
            "/api/index/neighbours": self.neighbours,
            "/api/index/projection": self.projection,
        }

    # -- overview --------------------------------------------------------------------------

    def overview(self, _req: Request) -> Reply:
        sources = {name: path.name for name, path in self.config.sources().items()}
        try:
            collections = self._index().collections()
            index_note = ""
        except ApiError as exc:
            collections, index_note = [], exc.message
        return 200, {
            "sources": sources,
            "collections": collections,
            "index_note": index_note,
            "note": "read-only: nothing here can change a record",
        }

    # -- SQLite stores ---------------------------------------------------------------------

    def _connect(self, source: str) -> sqlite3.Connection:
        path = self.config.sources().get(source)
        if path is None:
            raise ApiError(404, "no such source, or its file does not exist yet")
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            return conn
        except sqlite3.Error:
            raise ApiError(409, "the file cannot be opened") from None

    @staticmethod
    def _table_names(conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
        quoted = table.replace('"', '""')
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{quoted}")')]  # noqa: S608

    def _table(self, conn: sqlite3.Connection, name: str) -> tuple[str, list[str]]:
        if name not in self._table_names(conn):  # the name is checked against the schema
            raise ApiError(404, "no such table")
        return name.replace('"', '""'), self._columns(conn, name)

    def tables(self, req: Request) -> Reply:
        source = need(req.params, "source", 40)
        try:
            with closing(self._connect(source)) as conn:
                out = []
                for name in self._table_names(conn):
                    quoted, columns = self._table(conn, name)
                    count = conn.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0]  # noqa: S608
                    out.append({"name": name, "rows": count, "columns": columns})
        except sqlite3.Error:
            raise ApiError(409, "the file cannot be read") from None
        return 200, {"source": source, "tables": out}

    def rows(self, req: Request) -> Reply:
        source = need(req.params, "source", 40)
        table = need(req.params, "table", 200)
        offset = need_int(req.params, "offset", 0, 0, 10_000_000)
        limit = need_int(req.params, "limit", 50, 1, MAX_PAGE)
        query = (req.params.get("q") or "")[:200]
        sort = req.params.get("sort") or ""
        desc = req.params.get("desc") == "1"
        try:
            with closing(self._connect(source)) as conn:
                quoted, columns = self._table(conn, table)
                where, args = "", []
                if query:
                    like = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts = [
                        f"CAST(\"{c.replace(chr(34), chr(34) * 2)}\" AS TEXT) LIKE ? ESCAPE '\\'"
                        for c in columns
                    ]
                    where = " WHERE " + " OR ".join(parts)
                    args = [f"%{like}%"] * len(columns)
                order = ""
                if sort:
                    if sort not in columns:
                        raise ApiError(400, "no such column")
                    direction = "DESC" if desc else "ASC"
                    order = f' ORDER BY "{sort.replace(chr(34), chr(34) * 2)}" {direction}'
                total = conn.execute(f'SELECT count(*) FROM "{quoted}"{where}', args).fetchone()[0]  # noqa: S608
                got = conn.execute(
                    f'SELECT rowid, * FROM "{quoted}"{where}{order} LIMIT ? OFFSET ?',  # noqa: S608
                    [*args, limit, offset],
                ).fetchall()
        except sqlite3.Error:
            raise ApiError(409, "the table cannot be read here") from None
        shown = []
        for rec in got:
            cells, cut = [], 0
            for value in rec[1:]:
                text, more = _text(value)
                cells.append(text)
                cut += more
            shown.append({"rowid": rec[0], "cells": cells, "cut": cut})
        return 200, {
            "source": source,
            "table": table,
            "columns": columns,
            "rows": shown,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def row(self, req: Request) -> Reply:
        source = need(req.params, "source", 40)
        table = need(req.params, "table", 200)
        rowid = need_int(req.params, "rowid", 0, 0, 2**62)
        try:
            with closing(self._connect(source)) as conn:
                quoted, columns = self._table(conn, table)
                rec = conn.execute(
                    f'SELECT * FROM "{quoted}" WHERE rowid = ?',  # noqa: S608
                    [rowid],
                ).fetchone()
        except sqlite3.Error:
            raise ApiError(409, "the table cannot be read here") from None
        if rec is None:
            raise ApiError(404, "no such row")
        fields = []
        for name, value in zip(columns, rec, strict=True):
            text, more = _text(value, FULL_CHARS)
            fields.append({"name": name, "value": text, "cut": more})
        return 200, {"source": source, "table": table, "rowid": rowid, "fields": fields}

    # -- vector index ----------------------------------------------------------------------

    def _index(self) -> Any:
        from cognitive_mesh.memory import MemoryIndex

        try:
            return MemoryIndex.open_for_browse(self.config.paths.index)
        except MemoryIndexError as exc:
            raise ApiError(409, str(exc)) from None

    def _store(self) -> TraceStore:
        try:
            return TraceStore.open_existing(self.config.trace)
        except TraceStoreError:
            raise ApiError(409, "the trace database does not exist yet") from None

    def _collection(self, req: Request) -> str:
        name = need(req.params, "collection", 40)
        if name not in {c["name"] for c in self._index().collections()}:
            raise ApiError(404, "no such collection")
        return name

    def items(self, req: Request) -> Reply:
        name = self._collection(req)
        offset = need_int(req.params, "offset", 0, 0, 10_000_000)
        limit = need_int(req.params, "limit", 50, 1, MAX_PAGE)
        index = self._index()
        total = next(c["count"] for c in index.collections() if c["name"] == name)
        try:
            page = index.page(name, offset, limit)
        except MemoryIndexError as exc:
            raise ApiError(409, str(exc)) from None
        return 200, {
            "collection": name,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [{"id": i, "metadata": m} for i, m in page],
        }

    def _source_text(self, name: str, item_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        """The record the vector stands for, re-read from SQLite. The index holds no text."""
        if name == "decisions":
            with self._store() as store:
                for d in store.read_decisions().decisions:
                    if d.decision_id == item_id:
                        text, cut = _text(
                            f"{d.summary}\n\nchosen: {d.chosen}\n\nreasoning: {d.reasoning}",
                            FULL_CHARS,
                        )
                        return {
                            "found": True,
                            "kind": "decision",
                            "text": text,
                            "cut": cut,
                            "basis": d.basis.value,
                            "made_by": d.made_by.value,
                            "run_id": d.run_id,
                            "options": list(d.options),
                        }
            return {"found": False, "kind": "decision", "check": "missing"}
        try:
            run_id, step_id = str(meta["run_id"]), str(meta["step_id"])
            attempt, sha = int(meta["attempt_no"]), str(meta["text_sha256"])
        except (KeyError, TypeError, ValueError):
            return {"found": False, "kind": "step", "check": "malformed"}
        with self._store() as store:
            doc, check = verify_source(store, {}, run_id, step_id, attempt, sha)
        if doc is None or check is not SourceCheck.OK:
            return {"found": False, "kind": "step", "check": check.value}
        text, cut = _text(doc.text, FULL_CHARS)
        return {
            "found": True,
            "kind": "step",
            "text": text,
            "cut": cut,
            "basis": doc.basis,
            "capability": doc.capability,
            "result_status": doc.result_status,
            "run_id": run_id,
        }

    def item(self, req: Request) -> Reply:
        name = self._collection(req)
        item_id = need(req.params, "id", 500)
        index = self._index()
        try:
            found = index.item(name, item_id)
        except MemoryIndexError as exc:
            raise ApiError(409, str(exc)) from None
        if found is None:
            raise ApiError(404, "no such item")
        meta, vector = found
        norm = sum(x * x for x in vector) ** 0.5
        return 200, {
            "collection": name,
            "id": item_id,
            "metadata": meta,
            "vector": {"dimension": len(vector), "norm": round(norm, 4), "head": vector[:12]},
            "source": self._source_text(name, item_id, meta),
            "note": "text is re-read from the trace database; the index stores only vectors",
        }

    def neighbours(self, req: Request) -> Reply:
        name = self._collection(req)
        item_id = need(req.params, "id", 500)
        k = need_int(req.params, "k", 8, 1, 50)
        index = self._index()
        try:
            found = index.item(name, item_id)
            if found is None:
                raise ApiError(404, "no such item")
            near = index.neighbours(name, found[1], min(k + 1, 51))
        except MemoryIndexError as exc:
            raise ApiError(409, str(exc)) from None
        return 200, {
            "collection": name,
            "id": item_id,
            "neighbours": [
                {"id": i, "metadata": m, "similarity": s} for i, m, s in near if i != item_id
            ][:k],
            "note": "similarity is how close two vectors are, not whether either record is true",
        }

    def search(self, req: Request) -> Reply:
        from cognitive_mesh.memory import MemoryIndex

        query = need(req.params, "q", 2000)
        name = self._collection(req)
        k = need_int(req.params, "k", 8, 1, 50)
        floor = float(self.config_floor(req))
        with self._search_lock:
            try:
                embedder = self.embedder.get()
                index = MemoryIndex.open_existing(self.config.paths.index, embedder)
                with self._store() as store:
                    if name == "decisions":
                        found = index.search_decisions(query, store, k=k, min_similarity=floor)
                        return 200, {
                            "collection": name,
                            "hits": [
                                {
                                    "id": h.decision_id,
                                    "similarity": h.similarity,
                                    "title": h.summary,
                                    "excerpt": h.excerpt,
                                    "basis": h.basis,
                                }
                                for h in found.hits
                            ],
                            "unconfirmed": [
                                {
                                    "id": u.decision_id,
                                    "similarity": u.similarity,
                                    "why": u.reason.value,
                                }
                                for u in found.unverified
                            ],
                            "dropped_weak": found.dropped_weak,
                            "message": found.message,
                            "min_similarity": floor,
                        }
                    res = index.search(query, store, k=k, min_similarity=floor)
            except MemoryIndexError as exc:
                raise ApiError(409, str(exc)) from None
            except RuntimeError:
                raise ApiError(409, "search by meaning is not configured on this server") from None
        return 200, {
            "collection": name,
            "hits": [
                {
                    "id": f"{h.run_id}/{h.step_id}/{h.attempt_no}",
                    "similarity": h.similarity,
                    "title": f"{h.capability} ({h.result_status})",
                    "excerpt": h.excerpt,
                    "basis": h.basis or "",
                }
                for h in res.hits
            ],
            "unconfirmed": [
                {
                    "id": f"{u.run_id}/{u.step_id}/{u.attempt_no}",
                    "similarity": u.similarity,
                    "why": u.reason.value,
                }
                for u in res.unverified
            ],
            "dropped_weak": res.dropped_weak,
            "message": res.message,
            "min_similarity": floor,
            "note": res.note,
        }

    @staticmethod
    def config_floor(req: Request) -> float:
        raw = req.params.get("min_similarity")
        if raw in (None, ""):
            return 0.2
        try:
            value = float(raw)
        except ValueError:
            raise ApiError(400, "min_similarity must be a number") from None
        if not 0.0 <= value <= 1.0:
            raise ApiError(400, "min_similarity must be between 0 and 1")
        return value

    def projection(self, req: Request) -> Reply:
        import numpy as np

        name = self._collection(req)
        limit = need_int(req.params, "limit", 2000, 3, MAX_PROJECTION)
        try:
            ids, metas, vectors = self._index().vectors(name, limit)
        except MemoryIndexError as exc:
            raise ApiError(409, str(exc)) from None
        if len(ids) < 3:
            raise ApiError(409, "the collection has fewer than 3 items")
        matrix = np.asarray(vectors, dtype=np.float64)
        centred = matrix - matrix.mean(axis=0)
        _, _, vt = np.linalg.svd(centred, full_matrices=False)
        coords = centred @ vt[:2].T
        scale = float(np.abs(coords).max()) or 1.0
        coords = coords / scale
        points = [
            {
                "id": i,
                "x": round(float(x), 4),
                "y": round(float(y), 4),
                "label": str(m.get("capability") or m.get("basis") or ""),
            }
            for i, m, (x, y) in zip(ids, metas, coords, strict=True)
        ]
        total = next(c["count"] for c in self._index().collections() if c["name"] == name)
        return 200, {
            "collection": name,
            "points": points,
            "shown": len(points),
            "total": total,
            "method": "PCA via singular value decomposition, centred, first two components",
            "note": PROJECTION_NOTE,
        }
