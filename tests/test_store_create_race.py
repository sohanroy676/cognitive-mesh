"""Creating the experience graph file and the reviews file while another process does the same.

Found by running several `experience build` / `experience review` processes at once against files
that did not exist yet: the losers failed with "cannot write the experience graph" or "unsupported
reviews file version (0)". The schema was created as separate statements, so a second process could
see a half-made file, and a lost race was reported as an error. Now the schema is one transaction
and a file that another process has just created is simply used.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.experience import reviews as reviews_module
from cognitive_mesh.experience import store as graph_module
from cognitive_mesh.experience.models import ExperienceGraphError, Snapshot
from cognitive_mesh.experience.reviews import ReviewStore, ReviewStoreError

EMPTY = Snapshot(nodes=(), edges=(), diagnostics=())
STORES = [
    pytest.param(graph_module, id="graph"),
    pytest.param(reviews_module, id="reviews"),
]


def create(module: object, path: Path) -> None:
    if module is graph_module:
        graph_module.save(path, EMPTY)
    else:
        ReviewStore.create(path).close()


def header(path: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(path)
    try:
        return (
            int(conn.execute("PRAGMA application_id").fetchone()[0]),
            int(conn.execute("PRAGMA user_version").fetchone()[0]),
            int(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]),
        )
    finally:
        conn.close()


@pytest.mark.parametrize("module", STORES)
def test_a_file_another_process_just_created_is_used_not_an_error(
    module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "f.sqlite3"
    create(module, path)  # the "other process" finished
    before = header(path)
    monkeypatch.setattr(module, "_is_fresh", lambda conn: True)  # ... but we saw an empty file
    create(module, path)
    assert header(path)[:2] == before[:2]


@pytest.mark.parametrize("module", STORES)
def test_a_schema_that_fails_halfway_leaves_no_half_made_file(
    module: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "f.sqlite3"
    broken = module._SCHEMA + "\nCREATE TABLE meta (x);\nCREATE TABLE meta (x);\n"  # type: ignore[attr-defined]
    monkeypatch.setattr(module, "_SCHEMA", broken)
    with pytest.raises((ExperienceGraphError, ReviewStoreError)):
        create(module, path)
    assert header(path) == (0, 0, 0)  # nothing of the schema survived
    monkeypatch.undo()
    create(module, path)  # and a later, correct attempt works
    assert header(path)[2] > 0


@pytest.mark.parametrize("module", STORES)
def test_a_foreign_database_is_still_refused_untouched(module: object, tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (x TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises((ExperienceGraphError, ReviewStoreError), match="not "):
        create(module, path)
    assert header(path) == (0, 0, 1)


ONE_PROCESS = {
    "graph": (
        "import sys; from pathlib import Path; "
        "from cognitive_mesh.experience import store; "
        "from cognitive_mesh.experience.models import Snapshot; "
        "store.save(Path(sys.argv[1]), Snapshot(nodes=(), edges=(), diagnostics=()))"
    ),
    "reviews": (
        "import sys; from pathlib import Path; "
        "from cognitive_mesh.experience.reviews import ReviewStore; "
        "ReviewStore.create(Path(sys.argv[1])).close()"
    ),
}


@pytest.mark.parametrize("kind", ["graph", "reviews"])
@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_many_processes_creating_one_new_file_all_succeed(
    kind: str, round_no: int, tmp_path: Path
) -> None:
    path = tmp_path / f"{kind}{round_no}.sqlite3"
    procs = [
        subprocess.Popen(  # noqa: S603  (our own interpreter)
            [sys.executable, "-c", ONE_PROCESS[kind], str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(8)
    ]
    outputs = [(p.wait(timeout=120), p.stdout.read() if p.stdout else "") for p in procs]
    assert [code for code, _ in outputs] == [0] * 8, outputs
