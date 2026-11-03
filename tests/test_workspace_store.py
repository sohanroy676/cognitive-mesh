"""The workspace file (ADR-0036): persistence, soft removal, refusals."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cognitive_mesh.workspace import ProjectKind, WorkspaceError, WorkspaceStore

SHA = "a" * 40


def make(path: Path) -> None:
    with WorkspaceStore.create(path) as store:
        store.add_project("one", ProjectKind.LOCAL, "/x/one")
        store.add_project("two", ProjectKind.GITHUB, "https://github.com/o/two", commit=SHA)


def test_projects_persist_across_opens(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.open_existing(path) as store:
        got = store.projects()
    assert [(p.name, p.kind, p.commit) for p in got] == [
        ("one", ProjectKind.LOCAL, None),
        ("two", ProjectKind.GITHUB, SHA),
    ]


def test_duplicate_active_name_is_refused_but_reusable_after_removal(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store:
        with pytest.raises(WorkspaceError, match="already in the workspace"):
            store.add_project("one", ProjectKind.LOCAL, "/y")
        store.remove_project("one")
        store.add_project("one", ProjectKind.LOCAL, "/y")
        assert [p.location for p in store.projects() if p.name == "one"] == ["/y"]


def test_removal_keeps_rows_and_hides_links(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store:
        store.add_link("one", "two", "rpc", "note", "me")
        assert len(store.links()) == 1
        store.remove_project("two", now=datetime(2026, 1, 1, tzinfo=UTC))
        assert [p.name for p in store.projects()] == ["one"]
        assert store.links() == []
        everything = store.projects(include_removed=True)
    assert [p.name for p in everything] == ["one", "two"]
    assert everything[1].removed_at == datetime(2026, 1, 1, tzinfo=UTC)
    count = sqlite3.connect(path).execute("SELECT count(*) FROM declared_links").fetchone()[0]
    assert count == 1


def test_link_validation(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    with WorkspaceStore.create(path) as store:
        with pytest.raises(WorkspaceError, match="itself"):
            store.add_link("one", "one", "k", "n", "me")
        with pytest.raises(WorkspaceError, match="no project"):
            store.add_link("one", "nope", "k", "n", "me")
        with pytest.raises(WorkspaceError, match="empty"):
            store.add_link("one", "two", "k", "  ", "me")
        with pytest.raises(WorkspaceError, match="control"):
            store.add_link("one", "two", "k", "a\nb", "me")
        link = store.add_link("one", "two", "k", "token=ghp_" + "a" * 36, "me")
    assert "ghp_" not in link.note


def test_bad_names_and_unpinned_github_are_refused(tmp_path: Path) -> None:
    with WorkspaceStore.create(tmp_path / "ws.sqlite3") as store:
        with pytest.raises(WorkspaceError, match="not acceptable"):
            store.add_project("bad name", ProjectKind.LOCAL, "/x")
        with pytest.raises(WorkspaceError, match="recorded commit"):
            store.add_project("g", ProjectKind.GITHUB, "https://github.com/o/r")
        with pytest.raises(WorkspaceError, match="only a github"):
            store.add_project("l", ProjectKind.LOCAL, "/x")
            store.set_commit("l", SHA)


def test_read_only_open_cannot_write_and_never_creates(tmp_path: Path) -> None:
    missing = tmp_path / "none.sqlite3"
    with pytest.raises(WorkspaceError, match="does not exist"):
        WorkspaceStore.open_existing(missing)
    assert not missing.exists()
    path = tmp_path / "ws.sqlite3"
    make(path)
    with (
        WorkspaceStore.open_existing(path) as store,
        pytest.raises(WorkspaceError, match="read-only"),
    ):
        store.add_project("x", ProjectKind.LOCAL, "/x")


def test_foreign_and_newer_files_are_refused(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.sqlite3"
    conn = sqlite3.connect(foreign)
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    conn.close()
    with pytest.raises(WorkspaceError, match="not a workspace file"):
        WorkspaceStore.create(foreign)
    path = tmp_path / "ws.sqlite3"
    make(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(WorkspaceError, match="newer"):
        WorkspaceStore.open_existing(path)


def test_malformed_rows_are_refused_not_crashed_on(tmp_path: Path) -> None:
    path = tmp_path / "ws.sqlite3"
    make(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE projects SET added_at = 'yesterday-ish' WHERE name = 'one'")
    conn.commit()
    conn.close()
    with (
        WorkspaceStore.open_existing(path) as store,
        pytest.raises(WorkspaceError, match="malformed"),
    ):
        store.projects()
