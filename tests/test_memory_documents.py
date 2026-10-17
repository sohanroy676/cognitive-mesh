"""Which trace content is embedded (ADR-0014): eligibility, determinism, failure text."""

from pathlib import Path

from pydantic import JsonValue

from cognitive_mesh.memory import eligible_documents
from cognitive_mesh.memory.documents import MAX_TEXT_CHARS
from cognitive_mesh.traces import TraceStore
from memory_support import add_run, build_fixture_db, failure, success


def docs_of(db: Path, run_id: str):  # type: ignore[no-untyped-def]
    with TraceStore.open_existing(db) as store:
        return eligible_documents(store.get(run_id))


def test_a_successful_step_becomes_one_document_with_basis_and_a_stable_id(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    build_fixture_db(db)
    (doc,) = docs_of(db, "run-invoice")
    assert doc.doc_id == "run-invoice/explain/1"
    assert doc.result_status == "success"
    assert doc.basis == "model_unverified"
    assert "invoice.py" in doc.text
    assert "sales tax" in doc.text


def test_a_failed_step_is_eligible_and_uses_the_fixed_failure_text(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    build_fixture_db(db)
    (doc,) = docs_of(db, "run-down")
    assert doc.result_status == "failure"
    assert doc.basis is None
    assert "failure_kind: provider_unavailable" in doc.text
    assert "the model provider is unavailable" in doc.text


def test_an_interrupted_run_has_no_documents(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(db, "run-x", [("s", "c", {}, success("run-x", "s", "c", {}))], finish=False)
    assert docs_of(db, "run-x") == ()


def test_documents_are_deterministic_and_hash_the_exact_text(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    build_fixture_db(db)
    assert docs_of(db, "run-deps") == docs_of(db, "run-deps")
    (doc,) = docs_of(db, "run-deps")
    import hashlib

    assert doc.text_sha256 == hashlib.sha256(doc.text.encode()).hexdigest()


def test_different_content_gives_a_different_hash(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(db, "a", [("s", "c", {"p": "one"}, success("a", "s", "c", {"v": 1}))])
    add_run(db, "b", [("s", "c", {"p": "one"}, success("b", "s", "c", {"v": 2}))])
    assert docs_of(db, "a")[0].text_sha256 != docs_of(db, "b")[0].text_sha256


def test_very_long_output_is_cut_to_the_documented_limit(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    big: dict[str, JsonValue] = {"v": "x" * (MAX_TEXT_CHARS * 2)}
    add_run(db, "big", [("s", "c", {}, success("big", "s", "c", big))])
    assert len(docs_of(db, "big")[0].text) == MAX_TEXT_CHARS


def test_one_document_per_step_in_a_multi_step_run(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(
        db,
        "multi",
        [
            ("one", "c", {}, success("multi", "one", "c", {"a": 1})),
            ("two", "c", {}, failure("multi", "two", "c", "provider down")),
        ],
    )
    assert [d.step_id for d in docs_of(db, "multi")] == ["one", "two"]
