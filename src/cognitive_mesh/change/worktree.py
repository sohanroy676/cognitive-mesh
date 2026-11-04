"""The git worktree a change is made in (ADR-0042).

The checked-out branch and working tree of the project are never touched: the change lives on a new
branch in a separate worktree under a folder the person names. Every git call goes through
`workspace.fetch.run_git_with` (fixed argument lists, no shell, sanitised environment) and the
repository's hooks are switched off. Git's own output is never repeated.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cognitive_mesh.change.diffparse import DiffError, ParsedDiff, Refusal
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.impact.base_export import resolve_ref
from cognitive_mesh.impact.models import ImpactError
from cognitive_mesh.ingestion.secrets import redact_secrets
from cognitive_mesh.workspace.fetch import FetchError, GitResult, run_git_with

CHANGE_ID: Final = re.compile(r"[0-9a-f]{12,32}")
BRANCH_PREFIX: Final = "cogmesh/change-"
AUTHOR_NAME: Final = "cognitive-mesh"
AUTHOR_EMAIL: Final = "noreply@localhost"
_SHA = re.compile(r"[0-9a-f]{40}")
MAX_SUBJECT_CHARS: Final = 72


@dataclass(frozen=True)
class Worktree:
    toplevel: Path  # the worktree directory (a checkout of the whole repository)
    project_root: Path  # the project's folder inside it
    subdir: str  # the project's folder relative to the repository root ("" if it is the root)
    branch: str
    base_commit: str
    base_branch: str | None  # the branch checked out when the change started, if any


def _git(argv: list[str], cwd: Path, *, hooks: Path | None = None) -> GitResult:
    prefix = ["-c", f"core.hooksPath={hooks}"] if hooks is not None else []
    try:
        return run_git_with([*prefix, *argv], cwd)
    except FetchError:
        raise ChangeError("git could not be run (missing or timed out)") from None


def _ok(result: GitResult, message: str) -> str:
    if result.returncode != 0:
        raise ChangeError(message)
    return result.stdout.strip()


def repo_layout(project_root: Path) -> tuple[Path, str]:
    """(repository top level, the project's folder relative to it with '/' separators)."""
    top = Path(
        _ok(_git(["rev-parse", "--show-toplevel"], project_root), "the project is not in git")
    )
    prefix = _ok(_git(["rev-parse", "--show-prefix"], project_root), "the project is not in git")
    return top.resolve(), prefix.rstrip("/")


def branch_name(change_id: str) -> str:
    if not CHANGE_ID.fullmatch(change_id):
        raise ChangeError("the change id is not valid")
    return f"{BRANCH_PREFIX}{change_id}"


def branch_head(top: Path, branch: str) -> str | None:
    """The commit a local branch points at, or None if it does not exist."""
    done = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], top)
    found = done.stdout.strip()
    return found if done.returncode == 0 and _SHA.fullmatch(found) else None


def create_worktree(
    project_root: Path, worktrees_dir: Path, change_id: str, base_ref: str = "HEAD"
) -> Worktree:
    """Add `cogmesh/change-ID` as a new worktree of the project's repository at `base_ref`."""
    name = branch_name(change_id)
    top, subdir = repo_layout(project_root)
    folder = worktrees_dir.resolve()
    if folder == top or folder.is_relative_to(top) or top.is_relative_to(folder):
        raise ChangeError("the worktrees folder must be outside the project's repository")
    try:
        commit = resolve_ref(project_root, base_ref)
    except ImpactError as exc:
        raise ChangeError(str(exc)) from None
    current = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], project_root)
    base_branch = current.stdout.strip() if current.returncode == 0 else None
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], top).returncode == 0:
        raise ChangeError("a branch for this change already exists")
    target = folder / f"change-{change_id}"
    if target.exists():
        raise ChangeError("the worktree folder already exists")
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cogmesh-nohooks-") as hooks:
        _ok(
            _git(
                ["worktree", "add", "--no-track", "-b", name, str(target), commit],
                top,
                hooks=Path(hooks),
            ),
            "git could not create the worktree",
        )
    root = target / subdir if subdir else target
    return Worktree(target, root, subdir, name, commit, base_branch)


def _numstat(wt: Worktree) -> dict[str, int]:
    out = _ok(
        _git(["diff", "--cached", "--numstat", "--no-renames", "-z"], wt.toplevel),
        "git could not describe the change",
    )
    stats: dict[str, int] = {}
    for record in filter(None, out.split("\0")):
        added, removed, path = record.split("\t", 2)
        if added == "-" or removed == "-":
            raise DiffError(Refusal.BINARY)
        stats[path] = int(added) + int(removed)
    return stats


def apply_diff(wt: Worktree, diff_text: str, parsed: ParsedDiff) -> None:
    """Apply `diff_text` (already judged) in the worktree and check git saw what our parser saw.

    Raises DiffError(DOES_NOT_APPLY | NOT_FOUND | MALFORMED). The worktree is reset on failure.
    """
    normal = diff_text.replace("\r\n", "\n")
    if not normal.endswith("\n"):
        normal += "\n"
    with tempfile.TemporaryDirectory(prefix="cogmesh-patch-") as scratch:
        patch = Path(scratch) / "change.patch"
        patch.write_bytes(normal.encode("utf-8"))
        flags = ["apply", "--whitespace=nowarn", "--ignore-whitespace"]
        if wt.subdir:
            flags.append(f"--directory={wt.subdir}")
        checked = _git([*flags, "--check", str(patch)], wt.toplevel)
        if checked.returncode != 0:
            text = checked.stderr.lower()
            missing = "no such file" in text or "does not exist" in text
            raise DiffError(Refusal.NOT_FOUND if missing else Refusal.DOES_NOT_APPLY)
        applied = _git([*flags, str(patch)], wt.toplevel)
        if applied.returncode != 0:
            reset_worktree(wt)
            raise DiffError(Refusal.DOES_NOT_APPLY)
    _ok(_git(["add", "-A"], wt.toplevel), "git could not stage the change")
    try:
        stats = _numstat(wt)
        expected = {
            (f"{wt.subdir}/{f.path}" if wt.subdir else f.path): f.added + f.removed
            for f in parsed.files
        }
        if stats != expected:
            raise DiffError(Refusal.MALFORMED)
    except DiffError:
        reset_worktree(wt)
        raise


def reset_worktree(wt: Worktree) -> None:
    """Back to the base commit: staged and untracked changes of a refused draft are removed."""
    _ok(_git(["reset", "--hard", "--quiet", wt.base_commit], wt.toplevel), "git reset failed")
    _ok(_git(["clean", "-fdq"], wt.toplevel), "git clean failed")


def _subject(summary: str) -> str:
    one_line = " ".join(summary.split())
    clean, _ = redact_secrets("".join(ch for ch in one_line if ch.isprintable()))
    return (clean[:MAX_SUBJECT_CHARS] or "change") if clean.strip() else "change"


def commit_change(wt: Worktree, change_id: str, summary: str) -> str:
    """Commit the staged change on the change branch; the repository's hooks do not run."""
    message = (
        f"change: {_subject(summary)}\n\n"
        "Written by a model through cognitive-mesh; not reviewed yet.\n\n"
        f"Cognitive-Mesh-Change: {change_id}\n"
    )
    with tempfile.TemporaryDirectory(prefix="cogmesh-nohooks-") as hooks:
        _ok(
            _git(
                [
                    "-c",
                    f"user.name={AUTHOR_NAME}",
                    "-c",
                    f"user.email={AUTHOR_EMAIL}",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-q",
                    "--no-verify",
                    "-m",
                    message,
                ],
                wt.toplevel,
                hooks=Path(hooks),
            ),
            "git could not commit the change",
        )
    commit = _ok(_git(["rev-parse", "HEAD"], wt.toplevel), "git could not read the commit")
    if not _SHA.fullmatch(commit):
        raise ChangeError("git returned an unexpected commit id")
    return commit


def remove_worktree(project_root: Path, worktree: Path) -> None:
    """Remove only the worktree folder; the branch is never deleted (a rule of this repository)."""
    top, _ = repo_layout(project_root)
    _ok(
        _git(["worktree", "remove", "--force", str(worktree)], top),
        "git could not remove the worktree",
    )
