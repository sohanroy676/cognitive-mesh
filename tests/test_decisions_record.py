"""Recording decisions (ADR-0039): validation, citations, append-only storage, git links."""

import sqlite3
from pathlib import Path

import pytest

from cognitive_mesh.decisions.models import (
    CitationKind,
    DecisionBasis,
    DecisionError,
    LinkHow,
    LinkKind,
    MadeBy,
)
from cognitive_mesh.decisions.record import record_decision
from cognitive_mesh.traces import SCHEMA_VERSION, TraceStore
from decisions_support import git, make_app, make_db, person


def test_schema_is_version_eight() -> None:
    assert SCHEMA_VERSION == 8


def test_a_person_decision_is_stored_and_read_back_in_a_new_connection(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        decision, links = record_decision(
            store,
            summary="Keep the helper",
            options=["keep", "inline"],
            chosen="keep",
            reasoning="Two modules use it.\nSecond line.",
            made_by=MadeBy.PERSON,
            recorder="alice",
            cite_nodes=["target:module:a.py"],
            cite_files=["b.py"],
            root=app,
            run_id="run-1",
            step_id="s1",
        )
    assert links == ()  # tmp_path is not a git work tree
    assert decision.basis is DecisionBasis.PERSON
    assert decision.app_id == "demo"
    assert decision.fingerprint is not None
    file_cite = next(c for c in decision.citations if c.kind is CitationKind.FILE)
    assert file_cite.target == "b.py"
    assert file_cite.sha256 is not None and len(file_cite.sha256) == 64

    with TraceStore.open_existing(db) as store:
        listing = store.read_decisions()
    assert listing.malformed == ()
    assert listing.decisions == (decision,)


def test_the_id_depends_on_the_content_and_each_recording_is_its_own_record(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        ids = {person(store).decision_id for _ in range(3)}
    assert len(ids) == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": "   "},
        {"summary": "x" * 301},
        {"chosen": ""},
        {"reasoning": "bad\x00text"},
        {"summary": "line\nbreak"},  # only reasoning may be multi-line
        {"options": ["o"] * 11},
        {"options": ["x" * 201]},
        {"reasoning": "y" * 4001},
        {"cite_nodes": ["no-colon-here"]},
        {"cite_nodes": ["claim:a", "claim:a"]},
    ],
)
def test_invalid_records_are_refused_and_nothing_is_stored(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        with pytest.raises(DecisionError):
            person(store, **overrides)
        assert store.read_decisions().decisions == ()


def test_error_messages_never_echo_the_given_text(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    marker = "UNIQUE-MARKER-42"
    with TraceStore.create(db) as store:
        for bad in (
            {"summary": marker + "\x00"},
            {"cite_nodes": [marker]},
            {"supersedes": "decision:" + marker},
        ):
            with pytest.raises(Exception) as caught:  # noqa: PT011 - checking the text
                person(store, **bad)
            assert marker not in str(caught.value)


def test_a_secret_in_the_reasoning_is_redacted_before_storing(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    with TraceStore.create(db) as store:
        decision = person(store, reasoning=f"We use key {secret} here")
    assert secret not in decision.reasoning
    assert "[REDACTED" in decision.reasoning
    assert "reasoning" in decision.redactions
    with TraceStore.open_existing(db) as store:
        stored = store.read_decisions().decisions[0]
    assert secret not in stored.reasoning
    raw = sqlite3.connect(db).execute("SELECT reasoning FROM decisions").fetchone()[0]
    assert secret not in raw


@pytest.mark.parametrize(
    "path", ["../x.py", "/etc/passwd", "C:/x.py", "a\\b.py", "a//b.py", "./a.py"]
)
def test_unsafe_file_paths_are_refused(tmp_path: Path, path: str) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store, pytest.raises(DecisionError):
        person(store, cite_files=[path], root=app)


def test_citing_a_missing_file_or_citing_without_a_root_is_refused(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    with TraceStore.create(db) as store:
        with pytest.raises(DecisionError, match="not found"):
            person(store, cite_files=["nope.py"], root=app)
        with pytest.raises(DecisionError, match="needs --root"):
            person(store, cite_files=["a.py"])


def test_a_sensitive_file_cannot_be_cited_because_ingestion_never_opens_it(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    (app / ".env").write_text("TOKEN=value\n", encoding="utf-8")
    with TraceStore.create(db) as store, pytest.raises(DecisionError, match="not found"):
        person(store, cite_files=[".env"], root=app)


def test_supersession_and_citation_rules(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        first = person(store)
        second = person(store, supersedes=first.decision_id, cite_decisions=[first.decision_id])
        assert second.supersedes == first.decision_id
        with pytest.raises(DecisionError, match="does not exist"):
            person(store, supersedes="decision:" + "e" * 32)
        with pytest.raises(DecisionError, match="does not exist"):
            person(store, cite_decisions=["decision:" + "1" * 32])
        with pytest.raises(DecisionError, match="another application"):
            person(store, app_id="other", supersedes=first.decision_id)
        with pytest.raises(DecisionError, match="another application"):
            person(store, app_id="other", cite_decisions=[first.decision_id])
        assert len(store.read_decisions().decisions) == 2


def test_run_and_step_must_exist_and_a_step_needs_its_run(tmp_path: Path) -> None:
    # Changed by ADR-0040: a decision may name a run without a step (the conductor's plan decision).
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        assert person(store, run_id="run-1", step_id="s1").run_id == "run-1"
        with pytest.raises(DecisionError, match="does not exist"):
            person(store, run_id="run-1", step_id="nope")
        with pytest.raises(DecisionError, match="does not exist"):
            person(store, run_id="no-such-run")
        with pytest.raises(DecisionError):  # a step without its run
            person(store, step_id="s1")
        whole_run = person(store, run_id="run-1")
        assert (whole_run.run_id, whole_run.step_id) == ("run-1", None)


def test_a_model_decision_is_always_unverified_and_needs_a_recorded_call(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    model_args: dict[str, object] = {
        "made_by": MadeBy.MODEL,
        "recorder": None,
        "provider": "stub",
        "model": "m1",
        "role": "planner",
    }
    with TraceStore.create(db) as store:
        decision = person(store, **model_args)
        assert decision.basis is DecisionBasis.MODEL_UNVERIFIED
        assert decision.recorder is None
        with pytest.raises(DecisionError, match="model call"):
            person(store, agent_task_id="run-1-task.s1.1", **model_args)
        with pytest.raises(DecisionError):  # a model decision must name provider, model and role
            person(store, **{**model_args, "provider": None})
        with pytest.raises(DecisionError):  # a person's decision must not carry model fields
            person(store, provider="stub")


def test_a_model_decision_joins_its_recorded_model_call(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        store._conn.execute(  # noqa: SLF001 - a call row to join to
            "INSERT INTO model_calls (run_id, agent_task_id, call_no, step_id, entry_id, provider,"
            " model, role, task_type, latency_ms, prompt_tokens, completion_tokens, result)"
            " VALUES ('run-1', 'run-1-task.s1.1', 1, 's1', NULL, 'stub', 'm1', 'planner',"
            " 'dependencies_of', 5, NULL, NULL, 'ok')"
        )
        decision = person(
            store,
            made_by=MadeBy.MODEL,
            recorder=None,
            provider="stub",
            model="m1",
            role="planner",
            agent_task_id="run-1-task.s1.1",
            run_id="run-1",
            step_id="s1",
        )
    assert decision.agent_task_id == "run-1-task.s1.1"


def test_decision_tables_are_append_only(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        decision = person(store, cite_nodes=["claim:" + "a" * 32])
    conn = sqlite3.connect(db)
    for statement in (
        "UPDATE decisions SET summary = 'x'",
        "DELETE FROM decisions",
        "UPDATE decision_citations SET target = 'x:y'",
        "DELETE FROM decision_citations",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(statement)
    assert decision.decision_id


def test_a_malformed_stored_row_is_reported_not_dropped(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        good = person(store)
        bad = person(store, summary="the other one")
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER decisions_no_update")
    conn.execute(
        "UPDATE decisions SET options_json = 'not json' WHERE decision_id = ?", (bad.decision_id,)
    )
    conn.commit()
    with TraceStore.open_existing(db) as store:
        listing = store.read_decisions()
    assert [d.decision_id for d in listing.decisions] == [good.decision_id]
    assert listing.malformed == (bad.decision_id,)


def test_a_decision_whose_superseded_row_is_malformed_is_malformed_too(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        first = person(store)
        second = person(store, supersedes=first.decision_id)
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER decisions_no_update")
    conn.execute(
        "UPDATE decisions SET fingerprint = 'zz' WHERE decision_id = ?", (first.decision_id,)
    )
    conn.commit()
    with TraceStore.open_existing(db) as store:
        listing = store.read_decisions()
    assert listing.decisions == ()
    assert set(listing.malformed) == {first.decision_id, second.decision_id}


def test_migration_five_to_six_adds_the_tables_and_keeps_old_data(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    for table in ("change_events", "changes", "decision_links", "decision_citations", "decisions"):
        conn.execute(f"DROP TABLE {table}")  # noqa: S608 - fixed names
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="older version"):
        TraceStore.open_existing(db)
    with TraceStore.create(db) as store:
        assert person(store).summary
        assert len(store.list().runs) == 1
    with TraceStore.open_existing(db) as store:
        assert len(store.read_decisions().decisions) == 1


def test_links_are_appended_and_a_link_to_nothing_is_refused(tmp_path: Path) -> None:
    from cognitive_mesh.decisions.models import DecisionLink
    from cognitive_mesh.traces import utc_now

    db = make_db(tmp_path)
    with TraceStore.create(db) as store:
        decision = person(store)
        store.add_decision_link(
            DecisionLink(
                decision_id=decision.decision_id,
                kind=LinkKind.PR,
                value="org/repo#12",
                how=LinkHow.DECLARED,
                recorder="alice",
                created_at=utc_now(),
            )
        )
        with pytest.raises(DecisionError, match="no decision"):
            store.add_decision_link(
                DecisionLink(
                    decision_id="decision:" + "f" * 32,
                    kind=LinkKind.BRANCH,
                    value="main",
                    how=LinkHow.DECLARED,
                    recorder="alice",
                    created_at=utc_now(),
                )
            )
        listing = store.read_decisions()
    assert [(link.kind, link.value, link.how) for link in listing.links] == [
        (LinkKind.PR, "org/repo#12", LinkHow.DECLARED)
    ]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (LinkKind.COMMIT, "not-hex"),
        (LinkKind.COMMIT, "abc"),
        (LinkKind.BRANCH, "has space"),
        (LinkKind.BRANCH, ""),
        (LinkKind.PR, "x;rm -rf"),
        (LinkKind.PR, "a" * 301),
    ],
)
def test_malformed_link_values_are_refused(kind: LinkKind, value: str) -> None:
    from cognitive_mesh.decisions.models import DecisionLink
    from cognitive_mesh.traces import utc_now

    with pytest.raises(DecisionError, match=r"malformed|hexadecimal|reference"):
        DecisionLink(
            decision_id="decision:" + "a" * 32,
            kind=kind,
            value=value,
            how=LinkHow.DECLARED,
            recorder="alice",
            created_at=utc_now(),
        )


def test_the_commit_and_branch_of_a_git_root_are_stored_as_observed(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    git(app, "init", "-q", "-b", "feature/x")
    git(app, "add", ".")
    git(app, "commit", "-q", "-m", "init")
    head = git(app, "rev-parse", "HEAD")
    with TraceStore.create(db) as store:
        _, links = record_decision(
            store,
            summary="s",
            options=["a"],
            chosen="a",
            reasoning="r",
            made_by=MadeBy.PERSON,
            recorder="alice",
            root=app,
        )
        stored = store.read_decisions().links
    assert {(link.kind, link.value, link.how) for link in links} == {
        (LinkKind.COMMIT, head, LinkHow.OBSERVED),
        (LinkKind.BRANCH, "feature/x", LinkHow.OBSERVED),
    }
    assert len(stored) == 2


def test_a_detached_head_records_the_commit_only(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    app = make_app(tmp_path / "demo")
    git(app, "init", "-q")
    git(app, "add", ".")
    git(app, "commit", "-q", "-m", "init")
    git(app, "checkout", "-q", "--detach")
    with TraceStore.create(db) as store:
        _, links = record_decision(
            store,
            summary="s",
            options=["a"],
            chosen="a",
            reasoning="r",
            made_by=MadeBy.PERSON,
            recorder="alice",
            root=app,
        )
    assert [link.kind for link in links] == [LinkKind.COMMIT]


def test_an_unusable_root_is_a_decision_error(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with TraceStore.create(db) as store, pytest.raises(DecisionError, match="root"):
        person(store, root=tmp_path / "missing")
