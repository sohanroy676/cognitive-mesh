"""Decisions in the experience graph (format 7), source checks and the outcomes file (ADR-0039)."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.experience import (
    DiagnosticCode,
    EdgeKind,
    ExperienceGraph,
    NodeKind,
    SourceStatus,
    build_from_traces,
    check_sources,
)
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.build import file_node_id
from cognitive_mesh.experience.models import FORMAT_VERSION, ExperienceGraphError
from cognitive_mesh.experience.outcomes import (
    OutcomeResult,
    OutcomeSource,
    OutcomeStore,
    OutcomeStoreError,
)
from cognitive_mesh.traces import TraceStore
from decisions_support import make_app, make_db, person
from experience_support import deps_step
from experience_support import make_db as make_experience_db

FP = "1" * 64


def _snapshot(db: Path):  # type: ignore[no-untyped-def]
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def test_decisions_files_and_edges_join_the_graph(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        first = person(store, cite_files=["b.py"], root=app)
        second = person(
            store,
            summary="again",
            cite_files=["a.py"],
            cite_decisions=[first.decision_id],
            supersedes=first.decision_id,
            root=app,
            run_id="run-1",
            step_id="s1",
        )
    graph = ExperienceGraph(_snapshot(db))
    assert {n.id for n in graph.nodes(NodeKind.DECISION)} == {first.decision_id, second.decision_id}
    node = graph.node(first.decision_id)
    assert (node.basis, node.app_id, node.name) == ("person", "demo", first.summary)
    assert graph.node(file_node_id("demo", "b.py")).kind is NodeKind.FILE
    kinds = {(e.target, e.kind) for e in graph.out_edges(second.decision_id)}
    assert (first.decision_id, EdgeKind.SUPERSEDES) in kinds
    assert (first.decision_id, EdgeKind.CITES) in kinds
    assert (file_node_id("demo", "a.py"), EdgeKind.CITES) in kinds
    file_edge = next(
        e for e in graph.out_edges(first.decision_id) if e.target == file_node_id("demo", "b.py")
    )
    cited = next(c for c in first.citations if c.target == "b.py")
    assert file_edge.refs[0].file_sha256 == cited.sha256
    assert file_edge.refs[0].decision_id == first.decision_id


def test_decided_in_links_to_the_run_node_only_when_it_exists(tmp_path: Path) -> None:
    db = make_experience_db(
        tmp_path, {"run-1": ([deps_step("run-1", "s1", "module:a.py", ["module:b.py"])], FP)}
    )
    with TraceStore.create(db) as store:
        d = person(store, run_id="run-1", step_id="s1")
    graph = ExperienceGraph(_snapshot(db))
    assert [e.target for e in graph.out_edges(d.decision_id, {EdgeKind.DECIDED_IN})] == [
        "run:run-1"
    ]


def test_a_cited_node_is_linked_when_it_exists_and_not_invented_when_it_does_not(
    tmp_path: Path,
) -> None:
    db = make_experience_db(
        tmp_path, {"run-1": ([deps_step("run-1", "s1", "module:a.py", ["module:b.py"])], FP)}
    )
    graph0 = ExperienceGraph(_snapshot(db))
    claim = next(n.id for n in graph0.claims())
    with TraceStore.create(db) as store:
        d = person(store, cite_nodes=[claim, "claim:" + "9" * 32])
    graph = ExperienceGraph(_snapshot(db))
    assert [e.target for e in graph.out_edges(d.decision_id, {EdgeKind.CITES})] == [claim]
    assert not graph.has_node("claim:" + "9" * 32)
    assert [e.source for e in graph.in_edges(claim, {EdgeKind.CITES})] == [d.decision_id]


def test_a_rebuild_keeps_every_citation_and_is_deterministic(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        person(store, cite_files=["a.py", "b.py"], root=app)
    one, two = _snapshot(db), _snapshot(db)
    assert one == two
    assert graph_store.save(tmp_path / "g1", one) == graph_store.save(tmp_path / "g2", two)
    loaded = graph_store.load(tmp_path / "g1")
    assert loaded.snapshot == one
    cites = [e for e in loaded.snapshot.edges if e.kind is EdgeKind.CITES]
    assert len(cites) == 2


def test_the_graph_file_round_trips_decisions_and_format_six_is_refused(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        person(store)
    path = tmp_path / "g"
    graph_store.save(path, _snapshot(db))
    assert FORMAT_VERSION == 7
    assert len(graph_store.load(path).snapshot.nodes) >= 1
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '6' WHERE key = 'format_version'")
    conn.commit()
    conn.close()
    with pytest.raises(ExperienceGraphError, match="experience build"):
        graph_store.load(path)


def test_a_decision_edge_with_no_trace_reference_is_impossible() -> None:
    from cognitive_mesh.experience.models import TraceRef

    with pytest.raises(ValueError, match="names a run or a decision"):
        TraceRef()


# -- source checks ------------------------------------------------------------------------


def test_check_sources_reports_decisions_and_their_files_ok(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        d = person(store, cite_files=["b.py"], root=app)
    snapshot = _snapshot(db)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snapshot, store)
    assert report.statuses[d.decision_id] is SourceStatus.OK
    assert report.statuses[file_node_id("demo", "b.py")] is SourceStatus.OK
    assert report.all_ok


def test_a_decision_row_edited_behind_the_programs_back_is_unreadable_not_ok(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        d = person(store)
    snapshot = _snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER decisions_no_update")
    conn.execute("UPDATE decisions SET chosen = 'something else'")
    conn.commit()
    conn.close()
    with TraceStore.open_existing(db) as store:
        report = check_sources(snapshot, store)
        rebuilt = build_from_traces(store)
    assert report.statuses[d.decision_id] is SourceStatus.UNREADABLE
    assert not report.all_ok
    assert any(x.node_id == d.decision_id for x in report.diagnostics)
    assert d.decision_id not in {n.id for n in rebuilt.nodes}
    assert [x.code for x in rebuilt.diagnostics if x.node_id == d.decision_id] == [
        DiagnosticCode.MALFORMED_DECISION
    ]


def test_check_sources_reports_a_decision_missing_from_the_store(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        d = person(store)
    snapshot = _snapshot(db)
    (tmp_path / "elsewhere").mkdir()
    other = make_db(tmp_path / "elsewhere")
    with TraceStore.open_existing(other) as store:
        report = check_sources(snapshot, store)
    assert report.statuses[d.decision_id] is SourceStatus.MISSING


# -- outcomes file ----------------------------------------------------------------------------


def test_an_outcome_can_be_recorded_for_a_decision_but_a_promotion_cannot(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    decision_id = "decision:" + "a" * 32
    with OutcomeStore.create(path) as outcomes:
        row = outcomes.add(
            decision_id, FP, OutcomeSource.CHECK, OutcomeResult.SUCCEEDED, "ran", "me"
        )
        assert row.claim_id == decision_id
        with pytest.raises(OutcomeStoreError, match="only a claim can be promoted"):
            outcomes.add(
                decision_id, FP, OutcomeSource.PROMOTION, OutcomeResult.REJECTED, "x", "me"
            )
        with pytest.raises(OutcomeStoreError, match="claim id or a decision id"):
            outcomes.add("module:a.py", FP, OutcomeSource.CHECK, OutcomeResult.SUCCEEDED, "x", "me")
    with OutcomeStore.open_existing(path) as outcomes:
        assert [o.claim_id for o in outcomes.list_outcomes(decision_id)] == [decision_id]


def test_a_version_two_outcomes_file_is_upgraded_to_three_on_write(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    OutcomeStore.create(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 2")
    conn.close()
    with OutcomeStore.open_existing(path) as outcomes:
        assert outcomes.list_outcomes() == []  # a version-2 file is readable as it is
    with OutcomeStore.create(path):
        pass
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 3


def test_a_newer_outcomes_file_is_refused_by_this_program(tmp_path: Path) -> None:
    path = tmp_path / "o.db"
    OutcomeStore.create(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 4")
    conn.close()
    with pytest.raises(OutcomeStoreError, match="newer than this program"):
        OutcomeStore.open_existing(path)
