"""Opt-in real public clone:

`RUN_LIVE_GITHUB=1 uv run pytest -m live tests/test_live_github.py`."""

import os
from pathlib import Path

import pytest

from cognitive_mesh.workspace.fetch import fetch_github

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_GITHUB") != "1", reason="set RUN_LIVE_GITHUB=1"),
]


def test_public_repository_is_fetched_at_a_full_commit(tmp_path: Path) -> None:
    path, commit = fetch_github("https://github.com/octocat/Hello-World", tmp_path / "cache")
    assert len(commit) == 40 and (path / "README").is_file()
