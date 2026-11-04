"""Export the files of a project folder at a git ref into a scratch directory (ADR-0041).

The repository is only read: `git rev-parse` and `git archive`, with a fixed argument list and the
sanitised environment of `workspace/fetch.run_git`. Nothing is checked out and no worktree is made.
"""

import re
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cognitive_mesh.impact.models import ImpactError
from cognitive_mesh.workspace.fetch import FetchError, run_git

MAX_FILES = 20_000
MAX_BYTES = 64 * 1024 * 1024
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@~^{}-]{0,199}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _git(argv: list[str], cwd: Path) -> str:
    try:
        done = run_git(argv, cwd)
    except FetchError:
        raise ImpactError("git could not be run for the base ref") from None
    if done.returncode != 0:
        raise ImpactError("the project folder is not in a git repository, or git refused")
    return done.stdout.strip()


def resolve_ref(root: Path, ref: str) -> str:
    """The full commit id of `ref` in the repository that holds `root`."""
    if not _REF.fullmatch(ref):
        raise ImpactError("the base ref is not a plain git ref")
    _git(["rev-parse", "--git-dir"], root)  # fails when `root` is not in a repository
    try:
        done = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root)
    except FetchError:
        raise ImpactError("git could not be run for the base ref") from None
    commit = done.stdout.strip()
    if done.returncode != 0 or not _SHA.fullmatch(commit):
        raise ImpactError("the base ref does not name a commit")
    return commit


def _extract(archive: Path, dest: Path) -> None:
    total = 0
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise ImpactError("the base export has too many files")
        for member in members:
            total += max(member.size, 0)
            if total > MAX_BYTES:
                raise ImpactError("the base export is too large")
        tar.extractall(dest, members=members, filter="data")  # noqa: S202 - filter="data"


@contextmanager
def exported_base(root: Path, ref: str) -> Iterator[tuple[Path, str]]:
    """Yield (scratch directory holding the project at `ref`, full commit id); remove it after."""
    commit = resolve_ref(root, ref)
    with tempfile.TemporaryDirectory(prefix="cogmesh-base-") as scratch:
        archive = Path(scratch) / "base.tar"
        out = Path(scratch) / "tree"
        out.mkdir()
        # Run inside `root` with `-- .`: git then archives that folder, with paths relative to it
        # (a `commit:path` tree-ish from a subfolder would archive nothing).
        _git(["archive", "--format=tar", f"--output={archive}", commit, "--", "."], root)
        _extract(archive, out)
        archive.unlink()
        yield out, commit
