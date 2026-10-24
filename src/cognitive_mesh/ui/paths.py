"""Check a folder the page asks the server to graph (ADR-0032 point 1).

Any local directory is allowed, as the user chose. What is refused: an unusable path, and any path
that would make the server reach beyond the local disk or follow a link out of the folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from cognitive_mesh.ui.errors import ApiError

MAX_PATH_CHARS: Final = 1024


def resolve_folder(text: str) -> Path:
    """The absolute path of an existing directory, or an `ApiError` with a fixed message.

    The path is made absolute without resolving links, so ingestion can still refuse a root that
    is itself a symlink or junction.
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
    folder = Path(os.path.abspath(cleaned))
    try:
        is_dir = folder.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        raise ApiError(409, "that folder does not exist or is not a directory")
    return folder
