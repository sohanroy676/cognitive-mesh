"""Re-deriving claims from the application as it is now (ADR-0016)."""

import shutil
from pathlib import Path

import pytest

from cognitive_mesh.experience.current import Holding, observe_current
from cognitive_mesh.ingestion import IngestionError
from cognitive_mesh.traces import source_fingerprint

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
UTIL = "module:src/dep_app/util.py"


@pytest.fixture
def app(tmp_path: Path) -> Path:
    copy = tmp_path / "app"
    shutil.copytree(FIXTURE, copy)
    return copy


def test_a_stated_dependency_holds_with_its_evidence(app: Path) -> None:
    got = observe_current(app).check("depends_on", A, B, "imports")
    assert got.holding is Holding.HOLDS
    assert got.evidence and all(len(e.sha256) == 64 for e in got.evidence)
    assert got.evidence[0].path == "src/dep_app/a.py"


def test_a_dependency_that_is_not_there_is_refuted(app: Path) -> None:
    now = observe_current(app)
    assert now.check("depends_on", A, UTIL, "imports").holding is Holding.REFUTED
    assert now.check("depends_on", A, B, "contains").holding is Holding.REFUTED  # other edge kind


def test_unknown_subject_is_neither_holding_nor_refuted(app: Path) -> None:
    got = observe_current(app).check("depends_on", "module:gone.py", B, "imports")
    assert got.holding is Holding.SUBJECT_ABSENT and got.evidence == ()


def test_unknown_predicate_never_validates(app: Path) -> None:
    got = observe_current(app).check("is_probably_fine", A, B, "imports")
    assert got.holding is Holding.SUBJECT_ABSENT


def test_unresolved_claims(app: Path) -> None:
    now = observe_current(app)
    unresolved = next(
        n
        for n in now._graph.unresolved()  # noqa: SLF001  # find a real unresolved id
    )
    assert now.check("is_unresolved", unresolved.id, None, None).holding is Holding.HOLDS
    assert now.check("is_unresolved", A, None, None).holding is Holding.REFUTED


def test_editing_the_code_changes_the_fingerprint_and_the_answer(app: Path) -> None:
    before = observe_current(app)
    a = app / "src" / "dep_app" / "a.py"
    a.write_text(a.read_text().replace("from . import b", "from . import util"), newline="\n")
    after = observe_current(app)
    assert after.fingerprint != before.fingerprint
    assert before.check("depends_on", A, B, "imports").holding is Holding.HOLDS
    assert after.check("depends_on", A, B, "imports").holding is Holding.REFUTED


def test_the_fingerprint_is_the_one_the_recorder_computes(app: Path) -> None:
    from cognitive_mesh.ingestion import ingest_application

    assert observe_current(app).fingerprint == source_fingerprint(ingest_application(app))


def test_a_missing_root_is_an_ingestion_error(tmp_path: Path) -> None:
    with pytest.raises(IngestionError):
        observe_current(tmp_path / "nope")
