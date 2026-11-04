"""The base export (ADR-0041): read-only git, bounded, and nothing escapes the scratch folder."""

import io
import tarfile
from pathlib import Path

import pytest

from cognitive_mesh.impact import base_export
from cognitive_mesh.impact.base_export import exported_base, resolve_ref
from cognitive_mesh.impact.models import ImpactError
from impact_support import git, make_workspace


def _tar(path: Path, members: list[tuple[str, bytes | str]]) -> None:
    with tarfile.open(path, "w") as tar:
        for name, body in members:
            if isinstance(body, str):  # a symlink target
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = body
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))


def test_export_holds_the_committed_files_not_the_working_tree(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    root = ws.roots["api"]
    (root / "app.py").write_text("changed = True\n", encoding="utf-8")
    (root / "untracked.py").write_text("x = 1\n", encoding="utf-8")
    with exported_base(root, "HEAD") as (tree, commit):
        assert "FastAPI" in (tree / "app.py").read_text(encoding="utf-8")
        assert not (tree / "untracked.py").exists()
        assert commit == git(root, "rev-parse", "HEAD")
        scratch = tree
    assert not scratch.exists()  # removed afterwards


def test_resolve_ref_names_a_commit_and_nothing_else(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    root = ws.roots["api"]
    git(root, "tag", "v1")
    assert resolve_ref(root, "v1") == git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/blob", git(root, "hash-object", "-w", "app.py"))
    for bad in ("refs/blob", "no-such", "v1;ls", "-x", "", "a" * 300, "HEAD\n"):
        with pytest.raises(ImpactError) as caught:
            resolve_ref(root, bad)
        assert bad.strip() == "" or bad not in str(caught.value)


def test_a_folder_that_is_not_in_a_repository_is_refused(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with (
        pytest.raises(ImpactError, match="git repository"),
        exported_base(tmp_path / "plain", "HEAD"),
    ):
        pass


def test_extraction_refuses_traversal_and_links_that_escape(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar"
    _tar(archive, [("../escape.txt", b"x"), ("ok.txt", b"y"), ("link", "../../outside")])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises((tarfile.TarError, ImpactError)):
        base_export._extract(archive, dest)
    assert not (tmp_path / "escape.txt").exists()


def test_extraction_is_bounded_in_files_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "a.tar"
    _tar(archive, [(f"f{i}.txt", b"abcd") for i in range(5)])
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(base_export, "MAX_FILES", 4)
    with pytest.raises(ImpactError, match="too many files"):
        base_export._extract(archive, dest)
    monkeypatch.setattr(base_export, "MAX_FILES", 100)
    monkeypatch.setattr(base_export, "MAX_BYTES", 10)
    with pytest.raises(ImpactError, match="too large"):
        base_export._extract(archive, dest)
    assert list(dest.iterdir()) == []  # checked before anything is written
