import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ui_support import World as UiWorld


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run each test in an empty cwd with no COGMESH_*/GROQ_API_KEY so a real .env can't leak in."""
    for name in list(os.environ):
        if name.startswith("COGMESH_") or name == "GROQ_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture(scope="module")
def ui_world(tmp_path_factory: pytest.TempPathFactory) -> "UiWorld":
    """One real world for the read-only web UI tests (built through the CLI, fake embedder)."""
    from cognitive_mesh import cli
    from memory_support import FakeEmbedder
    from ui_support import build_world

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli, "_make_embedder", lambda: FakeEmbedder())
        return build_world(tmp_path_factory.mktemp("ui"))
