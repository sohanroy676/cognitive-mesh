"""File-level dependency graph of a typed folder, saved once and loaded from disk afterwards.

This is the graph of the read-only UI (ADR-0032) with one change the person asked for: a folder
is read and parsed only the first time (or when they choose Rebuild); every later request loads
the saved graph file. The file is the same `DependencyGraph.to_dict()` JSON that
`cognitive-mesh graph DIR --json` writes, inside a small wrapper that records when it was built.
Nothing is ever executed. A saved graph is not refreshed by itself: the page says when it was
built so a stale picture is never mistaken for the current state of the folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from cognitive_mesh.graph import DependencyGraph, GraphError, build_dependency_graph
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.serve.http import ApiError
from cognitive_mesh.ui.errors import ApiError as UiApiError
from cognitive_mesh.ui.graphdata import graph_payload

FORMAT: Final = 1
MAX_PATH_CHARS: Final = 1024
MAX_SAVED_BYTES: Final = 200 * 1024 * 1024


def folder_from_text(text: str) -> Path:
    """The folder a person typed, made absolute without following links. Fixed messages only."""
    cleaned = text.strip()
    if not cleaned:
        raise ApiError(400, "enter a folder path")
    if len(cleaned) > MAX_PATH_CHARS:
        raise ApiError(400, f"the path is longer than {MAX_PATH_CHARS} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ApiError(400, "the path contains control characters")
    if cleaned.replace("/", "\\").startswith("\\\\"):
        raise ApiError(400, "network and device paths are not allowed")
    path = Path(os.path.abspath(cleaned))
    try:
        if path.is_dir():
            return path
    except OSError:
        pass
    raise ApiError(409, "that folder does not exist")


class FolderGraphs:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()  # one folder is read at a time: ingestion is disk heavy

    def _key(self, folder: Path) -> str:
        return hashlib.sha256(str(folder).casefold().encode("utf-8")).hexdigest()[:32]

    def _paths(self, folder: Path) -> tuple[Path, Path]:
        key = self._key(folder)
        return self.directory / f"{key}.json", self.directory / f"{key}.meta.json"

    def get(self, folder: Path, *, full: bool, rebuild: bool) -> dict[str, Any]:
        with self._lock:
            graph, source = None, None
            if not rebuild:
                graph, source = self._load(folder)
            if graph is None:
                graph, source = self._build_and_save(folder)
        try:
            return graph_payload(graph, folder, full=full, source=source)
        except UiApiError as exc:
            if not full:
                raise ApiError(exc.status, exc.message) from None
        # over the full-graph limit: show the first part, and the page says so
        try:
            return graph_payload(graph, folder, full=False, source=source)
        except UiApiError as exc:
            raise ApiError(exc.status, exc.message) from None

    def saved(self) -> list[dict[str, Any]]:
        """The folders that have a saved graph, newest first (small files, read at once)."""
        out: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            return out
        for meta in self.directory.glob("*.meta.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                out.append(
                    {
                        "folder": str(data["folder"]),
                        "built_at": str(data["built_at"]),
                        "nodes": int(data["nodes"]),
                        "edges": int(data["edges"]),
                    }
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(out, key=lambda row: row["built_at"], reverse=True)

    # -- disk ------------------------------------------------------------------------------

    def _load(self, folder: Path) -> tuple[DependencyGraph | None, dict[str, Any] | None]:
        data_path, _ = self._paths(folder)
        try:
            if not data_path.is_file() or data_path.stat().st_size > MAX_SAVED_BYTES:
                return None, None
            wrapper = json.loads(data_path.read_bytes())
            if wrapper.get("format") != FORMAT or wrapper.get("folder") != str(folder):
                return None, None
            graph = DependencyGraph.from_dict(wrapper["graph"])
            built_at = str(wrapper["built_at"])
        except (OSError, ValueError, KeyError, TypeError, RecursionError, GraphError):
            return None, None  # unreadable or foreign: build again instead of failing
        return graph, {"kind": "saved", "built_at": built_at, "path": str(data_path)}

    def _build_and_save(self, folder: Path) -> tuple[DependencyGraph, dict[str, Any]]:
        try:
            graph = build_dependency_graph(ingest_application(folder))
        except IngestionError as exc:
            raise ApiError(409, str(exc)[:300]) from None
        built_at = datetime.now(UTC).isoformat()
        data_path, meta_path = self._paths(folder)
        wrapper = {
            "format": FORMAT,
            "folder": str(folder),
            "built_at": built_at,
            "graph": graph.to_dict(),
        }
        meta = {
            "folder": str(folder),
            "built_at": built_at,
            "nodes": len(graph.nodes()),
            "edges": len(graph.edges()),
        }
        saved = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            _write_atomic(data_path, json.dumps(wrapper))
            _write_atomic(meta_path, json.dumps(meta))
        except OSError:
            saved = False  # the graph is still shown; the next request builds it again
        return graph, {"kind": "built", "built_at": built_at, "saved": saved}


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
