"""The memory index while another process holds Chroma's SQLite file.

Found by running 4 `memory index` processes at once on a new index directory: about one run in
five failed with "cannot open the memory index" because Chroma reported `database is locked`.
Opening is retried a few times for any error (the failures differ and all pass on a later try);
writing is retried only for a lock, so a real write failure is not delayed or hidden.
"""

from pathlib import Path
from typing import Any

import chromadb
import pytest

from cognitive_mesh.memory import MemoryIndexError
from cognitive_mesh.memory import index as index_module
from cognitive_mesh.traces import TraceStore
from memory_support import FakeEmbedder, build_fixture_db

LOCKED = RuntimeError("Query error: Database error: (code: 5) database is locked")
# The three failures seen when several processes create one new index at once (the last one comes
# from the half-built client Chroma caches in the process whose first try failed).
CREATION_RACE = [
    LOCKED,
    RuntimeError("error returned from database: (code: 1) table collections already exists"),
    AttributeError("'RustBindingsAPI' object has no attribute 'bindings'"),
]


class Flaky:
    """Raises `error` for the first `failures` calls, then behaves like the real thing."""

    def __init__(self, real: Any, failures: int, error: Exception) -> None:
        self.real, self.failures, self.error, self.calls = real, failures, error, 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return self.real(*args, **kwargs)


class Proxy:
    """A collection whose `upsert` is replaced; everything else is the real collection."""

    def __init__(self, real: Any, upsert: Any) -> None:
        self._real, self.upsert = real, upsert

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(index_module, "_sleep", waited.append)
    return waited


@pytest.mark.parametrize("error", CREATION_RACE, ids=["locked", "exists", "half-built"])
def test_opening_a_new_index_survives_a_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], error: Exception
) -> None:
    flaky = Flaky(chromadb.PersistentClient, 2, error)
    monkeypatch.setattr(chromadb, "PersistentClient", flaky)
    index = index_module.MemoryIndex.open_for_write(tmp_path / "idx", FakeEmbedder())
    assert index.count() == 0
    assert flaky.calls == 3 and len(sleeps) == 2  # two errors, two waits, then success


def test_an_index_that_cannot_be_opened_fails_with_the_usual_message_after_a_bounded_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    flaky = Flaky(chromadb.PersistentClient, 99, RuntimeError("disk is on fire"))
    monkeypatch.setattr(chromadb, "PersistentClient", flaky)
    with pytest.raises(MemoryIndexError, match="cannot open the memory index") as raised:
        index_module.MemoryIndex.open_for_write(tmp_path / "idx", FakeEmbedder())
    assert "fire" not in str(raised.value)  # the underlying text is never shown
    assert flaky.calls == len(index_module._BACKOFF_S) + 1 == len(sleeps) + 1


def test_reading_an_index_that_is_not_there_does_not_wait(
    tmp_path: Path, sleeps: list[float]
) -> None:
    with pytest.raises(MemoryIndexError, match="does not exist"):
        index_module.MemoryIndex.open_existing(tmp_path / "missing", FakeEmbedder())
    assert sleeps == []


def _flaky_index(tmp_path: Path, error: Exception) -> tuple[Any, Flaky, Path]:
    db = tmp_path / "t.sqlite3"
    build_fixture_db(db)
    index = index_module.MemoryIndex.open_for_write(tmp_path / "idx", FakeEmbedder())
    flaky = Flaky(index._col.upsert, 1, error)
    index._col = Proxy(index._col, flaky)
    return index, flaky, db


def test_a_locked_write_is_retried_and_does_not_duplicate(
    tmp_path: Path, sleeps: list[float]
) -> None:
    index, flaky, db = _flaky_index(tmp_path, LOCKED)
    with TraceStore.open_existing(db) as store:
        report = index.index_traces(store)
    assert report.added > 0 and flaky.calls >= 2 and len(sleeps) == 1
    assert index.count() == report.added  # the retried batch was written once


def test_a_write_that_fails_for_another_reason_is_not_retried(
    tmp_path: Path, sleeps: list[float]
) -> None:
    index, flaky, db = _flaky_index(tmp_path, RuntimeError("no space left"))
    with TraceStore.open_existing(db) as store, pytest.raises(MemoryIndexError, match="write"):
        index.index_traces(store)
    assert flaky.calls == 1 and sleeps == []
