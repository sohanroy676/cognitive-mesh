"""Load a saved dependency graph file for the Graph tab (ADR-0035).

The file is what `cognitive-mesh graph DIR --json` writes. It is read, parsed and validated
through the graph models; nothing is ingested or run. Every refusal has a fixed message that
never contains text from the file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from cognitive_mesh.graph import DependencyGraph, GraphError
from cognitive_mesh.ui.errors import ApiError

MAX_BYTES: Final = 25 * 1024 * 1024
NOT_A_GRAPH: Final = "that file is not a saved dependency graph"


def load_saved_graph(path: Path) -> tuple[DependencyGraph, dict[str, Any]]:
    """The graph in a saved file, and facts about the file for the page to show."""
    try:
        if path.is_symlink():
            raise ApiError(409, NOT_A_GRAPH)
        info = path.stat()
        if info.st_size > MAX_BYTES:
            limit = MAX_BYTES // (1024 * 1024)
            raise ApiError(409, f"that file is larger than {limit} MB, the most the page loads")
        raw = path.read_bytes()
    except OSError:
        raise ApiError(409, NOT_A_GRAPH) from None
    try:
        graph = DependencyGraph.from_dict(json.loads(raw))
    except (ValueError, RecursionError, GraphError):
        raise ApiError(409, NOT_A_GRAPH) from None
    source = {
        "kind": "saved file",
        "path": str(path),
        "bytes": info.st_size,
        "modified": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
    }
    return graph, source
