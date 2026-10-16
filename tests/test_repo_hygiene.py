import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git not available")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    return subprocess.run(  # noqa: S603
        [GIT, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.prod.local", "x.pem", "x.key"])
def test_secret_files_are_git_ignored(name: str) -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert _git("check-ignore", "-q", name).returncode == 0, f"{name} is not ignored"


def test_no_env_files_tracked_except_example() -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = _git("ls-files").stdout.splitlines()
    env_files = [f for f in tracked if Path(f).name.startswith(".env")]
    assert set(env_files) <= {".env.example"}


def test_env_example_contains_only_placeholders() -> None:
    for raw in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        if "KEY" in name or "TOKEN" in name or "SECRET" in name:
            assert value == "", f"{name} must be empty in .env.example"
        assert not re.search(r"gsk_|sk-[A-Za-z0-9]{10,}", value)
