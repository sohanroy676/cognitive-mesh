"""The change worktree (ADR-0042): separate checkout, hooks off, git agrees with the parser."""

from pathlib import Path

import pytest

from cognitive_mesh.change.diffparse import DiffError, Refusal, parse_diff
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.worktree import (
    apply_diff,
    commit_change,
    create_worktree,
    remove_worktree,
    reset_worktree,
)
from impact_support import commit_all, git

NL = chr(10)
CID = "ab12cd34ef56"

CHANGE = (
    "--- a/app.py" + NL + "+++ b/app.py" + NL + "@@ -1,2 +1,2 @@" + NL
    + " one = 1" + NL + "-two = 2" + NL + "+two = 22" + NL
)  # fmt: skip
NEW = (
    "--- /dev/null" + NL + "+++ b/notes.txt" + NL + "@@ -0,0 +1,1 @@" + NL + "+hello" + NL
)  # fmt: skip


def make_repo(tmp: Path, sub: str | None = None) -> tuple[Path, Path]:
    repo = tmp / "repo"
    root = repo / sub if sub else repo
    root.mkdir(parents=True)
    (root / "app.py").write_text("one = 1" + NL + "two = 2" + NL, encoding="utf-8", newline="")
    git(repo, "init", "-q")
    commit_all(repo, "base")
    return repo, root


def test_worktree_is_separate_and_the_checked_out_branch_is_untouched(tmp_path: Path) -> None:
    repo, root = make_repo(tmp_path)
    (root / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD")
    wt = create_worktree(root, tmp_path / "wts", CID)
    assert wt.branch == f"cogmesh/change-{CID}" and wt.base_commit == head
    parsed = parse_diff(CHANGE)
    apply_diff(wt, CHANGE, parsed)
    commit = commit_change(wt, CID, "Change two\nsecond line")
    assert (wt.project_root / "app.py").read_text(encoding="utf-8") == "one = 1\ntwo = 22\n"
    assert (root / "app.py").read_text(encoding="utf-8") == "one = 1\ntwo = 2\n"
    assert not (wt.project_root / "dirty.txt").exists()  # the base is a commit, not the folder
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "rev-parse", f"refs/heads/{wt.branch}") == commit
    body = git(repo, "log", "-1", "--format=%an <%ae>%n%s%n%b", commit)
    assert "cognitive-mesh <noreply@localhost>" in body
    assert "change: Change two second line" in body
    assert f"Cognitive-Mesh-Change: {CID}" in body


def test_a_project_in_a_subfolder_is_patched_relative_to_that_folder(tmp_path: Path) -> None:
    repo, root = make_repo(tmp_path, "services/api")
    wt = create_worktree(root, tmp_path / "wts", CID)
    assert wt.subdir == "services/api"
    apply_diff(wt, CHANGE + NEW, parse_diff(CHANGE + NEW))
    assert (wt.project_root / "notes.txt").read_text(encoding="utf-8").strip() == "hello"
    assert not (wt.toplevel / "notes.txt").exists()
    commit_change(wt, CID, "x")
    assert git(repo, "show", "--name-only", "--format=", wt.branch).splitlines() == [
        "services/api/app.py",
        "services/api/notes.txt",
    ]


def test_the_worktrees_folder_must_be_outside_the_repository(tmp_path: Path) -> None:
    repo, root = make_repo(tmp_path)
    for folder in (repo / "wts", repo, tmp_path):
        with pytest.raises(ChangeError, match="outside"):
            create_worktree(root, folder, CID)


def test_bad_ids_and_repeated_ids_are_refused(tmp_path: Path) -> None:
    _, root = make_repo(tmp_path)
    for bad in ("../x", "short", "UPPERCASE1234", "ab12cd34ef56;rm"):
        with pytest.raises(ChangeError, match="not valid"):
            create_worktree(root, tmp_path / "wts", bad)
    create_worktree(root, tmp_path / "wts", CID)
    with pytest.raises(ChangeError, match="already exists"):
        create_worktree(root, tmp_path / "wts", CID)


def test_a_project_outside_git_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ChangeError):
        create_worktree(plain, tmp_path / "wts", CID)


def test_a_diff_that_does_not_apply_leaves_the_worktree_clean(tmp_path: Path) -> None:
    _, root = make_repo(tmp_path)
    wt = create_worktree(root, tmp_path / "wts", CID)
    wrong = CHANGE.replace("-two = 2", "-two = 3")
    with pytest.raises(DiffError) as info:
        apply_diff(wt, wrong, parse_diff(wrong))
    assert info.value.code is Refusal.DOES_NOT_APPLY
    assert git(wt.toplevel, "status", "--porcelain") == ""
    missing = CHANGE.replace("app.py", "ghost.py")
    with pytest.raises(DiffError) as gone:
        apply_diff(wt, missing, parse_diff(missing))
    assert gone.value.code in (Refusal.NOT_FOUND, Refusal.DOES_NOT_APPLY)


def test_git_and_our_parser_must_agree(tmp_path: Path) -> None:
    _, root = make_repo(tmp_path)
    wt = create_worktree(root, tmp_path / "wts", CID)
    # a parser result that claims fewer lines than git saw is refused and rolled back
    lying = parse_diff(CHANGE.replace("-two = 2", "-two = 2").replace("+two = 22", "+two = 22"))
    bigger = CHANGE.replace(" one = 1", "-one = 1" + NL + "+one = 11").replace(
        "@@ -1,2 +1,2 @@", "@@ -1,2 +1,2 @@"
    )
    with pytest.raises(DiffError) as info:
        apply_diff(wt, bigger, lying)
    assert info.value.code is Refusal.MALFORMED
    assert git(wt.toplevel, "status", "--porcelain") == ""


def test_repository_hooks_never_run(tmp_path: Path) -> None:
    repo, root = make_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    for name in ("post-checkout", "pre-commit", "commit-msg", "post-commit"):
        hook = repo / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh{NL}echo ran > '{marker.as_posix()}'{NL}", encoding="utf-8")
        hook.chmod(0o755)
    wt = create_worktree(root, tmp_path / "wts", CID)
    apply_diff(wt, CHANGE, parse_diff(CHANGE))
    commit_change(wt, CID, "x")
    assert not marker.exists()


def test_reset_and_remove_keep_the_branch(tmp_path: Path) -> None:
    repo, root = make_repo(tmp_path)
    wt = create_worktree(root, tmp_path / "wts", CID)
    apply_diff(wt, CHANGE, parse_diff(CHANGE))
    reset_worktree(wt)
    assert git(wt.toplevel, "status", "--porcelain") == ""
    remove_worktree(root, wt.toplevel)
    assert not wt.toplevel.exists()
    assert git(repo, "rev-parse", "--verify", f"refs/heads/{wt.branch}")
