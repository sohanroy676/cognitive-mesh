"""Opt-in test with the real embedding model. Skipped unless RUN_LIVE_EMBEDDINGS=1.

The first run downloads ~90 MB from Hugging Face. It checks that real semantic ranking works and
prints the similarities used to choose the default threshold (run with `-s`).
"""

import os
from pathlib import Path

import pytest

from cognitive_mesh.memory import MemoryIndex, SentenceTransformerEmbedder
from cognitive_mesh.traces import TraceStore
from memory_support import build_fixture_db

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_EMBEDDINGS") != "1", reason="set RUN_LIVE_EMBEDDINGS=1"
    ),
]


def test_real_model_ranks_related_meaning_above_unrelated(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    build_fixture_db(db)
    embedder = SentenceTransformerEmbedder()
    with TraceStore.open_existing(db) as store:
        index = MemoryIndex.open_for_write(tmp_path / "idx", embedder)
        assert index.index_traces(store).added == 3
        for query, expected in [
            ("how are the totals and taxes on a bill calculated", "run-invoice"),
            ("what does module a import", "run-deps"),
            ("the language model could not be reached", "run-down"),
        ]:
            found = index.search(query, store, k=3, min_similarity=0.0)
            print(query, [(h.run_id, h.similarity) for h in found.hits])
            assert found.hits[0].run_id == expected
        unrelated = index.search(
            "chocolate cake recipe with frosting", store, k=3, min_similarity=0
        )
        print("unrelated", [(h.run_id, h.similarity) for h in unrelated.hits])
