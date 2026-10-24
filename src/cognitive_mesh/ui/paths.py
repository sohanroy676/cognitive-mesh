"""Check a folder the page asks the server to graph (ADR-0032 point 1).

Any local directory is allowed, as the user chose. What is refused: an unusable path, and any path
that would make the server reach beyond the local disk or follow a link out of the folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Literal

from cognitive_mesh.ui.errors import ApiError

MAX_PATH_CHARS: Final = 1024


def resolve_graph_source(text: str) -> tuple[Literal["folder", "file"], Path]:
    """A typed path as a folder to build, or a saved graph file (`.json`) to load (ADR-0035).

    The path is made absolute without resolving links, so ingestion can still refuse a root that
    is itself a symlink or junction, and a symlinked file can be refused. Failures are `ApiError`
    with fixed messages.
    """
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
            return "folder", path
        if path.is_file():
            if path.suffix.lower() == ".json":
                return "file", path
            raise ApiError(409, "that path is not a folder or a saved graph file (.json)")
    except OSError:
        pass
    raise ApiError(409, "that folder or file does not exist")
