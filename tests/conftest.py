import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run each test in an empty cwd with no COGMESH_*/GROQ_API_KEY so a real .env can't leak in."""
    for name in list(os.environ):
        if name.startswith("COGMESH_") or name == "GROQ_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
