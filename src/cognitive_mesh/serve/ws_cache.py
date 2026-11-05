"""The project-level graph of the workspace, analysed once and then kept.

Reading every file of every project is slow, so the result is kept in memory and in a file under
the home folder. It is reused until the workspace file changes (a project or link was added) or the
person asks for a fresh read; switching pages never triggers it. Because project files can change
without the workspace file changing, the reply says when the analysis was made.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognitive_mesh.serve.chat_store import clean_text
from cognitive_mesh.serve.graph_api import graph_payload
from cognitive_mesh.workspace.cli import load_workspace
from cognitive_mesh.workspace.models import WorkspaceError
from cognitive_mesh.workspace.render import to_json

FORMAT = 1


class WorkspaceGraphCache:
    def __init__(self, workspace: Path, cache_file: Path) -> None:
        self.workspace = workspace
        self.cache_file = cache_file
        self._lock = threading.Lock()
        self._memory: dict[str, Any] | None = None

    def _stamp(self) -> int | None:
        try:
            return self.workspace.stat().st_mtime_ns
        except OSError:
            return None

    def get(self, *, refresh: bool = False) -> dict[str, Any]:
        """`{"graph", "graph_error", "analysed_at", "from_cache"}`; never raises."""
        stamp = self._stamp()
        if stamp is None:
            return {"graph": None, "graph_error": None, "analysed_at": None, "from_cache": False}
        with self._lock:
            if not refresh:
                kept = self._kept(stamp)
                if kept is not None:
                    return {**kept["reply"], "from_cache": True}
            reply = self._analyse()
            entry = {"format": FORMAT, "stamp": stamp, "reply": reply}
            if reply["graph_error"] is None:  # a failure is shown, not kept
                self._memory = entry
                self._save(entry)
            return {**reply, "from_cache": False}

    def _kept(self, stamp: int) -> dict[str, Any] | None:
        entry = self._memory
        if entry is None:
            try:
                entry = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        if (
            not isinstance(entry, dict)
            or entry.get("format") != FORMAT
            or entry.get("stamp") != stamp
            or not isinstance(entry.get("reply"), dict)
        ):
            return None
        self._memory = entry
        return entry

    def _analyse(self) -> dict[str, Any]:
        analysed_at = datetime.now(UTC).isoformat()
        try:
            graph, _ = load_workspace(self.workspace)
            return {
                "graph": graph_payload(to_json(graph)),
                "graph_error": None,
                "analysed_at": analysed_at,
            }
        except WorkspaceError as exc:
            error = clean_text(str(exc), 300)
        except Exception as exc:  # the project list is shown regardless
            error = f"the projects' files could not be analysed ({type(exc).__name__})"
        return {"graph": None, "graph_error": error, "analysed_at": analysed_at}

    def _save(self, entry: dict[str, Any]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry), encoding="utf-8")
            tmp.replace(self.cache_file)
        except OSError:
            pass  # kept in memory only
