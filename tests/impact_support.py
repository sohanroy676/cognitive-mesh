"""Helpers for the ADR-0041 tests: a small two-project workspace on disk, `api` under git."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from cognitive_mesh.workspace import ProjectKind, WorkspaceStore
from cognitive_mesh.workspace.cli import load_workspace
from cognitive_mesh.workspace.graph import ProjectRead, WorkspaceGraph
from contracts_support import write_project

Files = dict[str, str]

API: Files = {
    "pyproject.toml": '[project]\nname = "apilib"\nversion = "1.2.0"\ndependencies = []\n',
    "src/apilib/__init__.py": "from .core import greet, shout\n\n__all__ = ['greet', 'shout']\n",
    "src/apilib/core.py": "def greet(): ...\n\n\ndef shout(): ...\n",
    "app.py": (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        '@app.get("/users")\ndef users(): ...\n\n\n'
        '@app.get("/users/{user_id}")\ndef user(user_id: int): ...\n\n\n'
        '@app.post("/orders")\ndef order(): ...\n'
    ),
    "db/001.sql": (
        "CREATE TABLE users (\n    id integer PRIMARY KEY,\n"
        "    email varchar(100) NOT NULL,\n    name text\n);\n"
    ),
    ".env.example": "API_URL=\nREGION=\n",
}

WEB: Files = {
    "pyproject.toml": (
        '[project]\nname = "web"\nversion = "0.1.0"\ndependencies = ["apilib>=1.0,<2"]\n'
    ),
    "src/web/client.py": (
        "import os\n\nimport requests\nfrom apilib import greet\n\n\n"
        "def users():\n    return requests.get('/users')\n\n\n"
        "def one():\n    return requests.get('/users/42')\n\n\n"
        "def order():\n    return requests.post('/orders')\n\n\n"
        "def url():\n    return os.environ['API_URL']\n\n\n"
        "def hello():\n    return greet()\n"
    ),
    "src/web/models.py": (
        "from sqlalchemy import Column, Integer, String\n\n\n"
        "class User(Base):\n    __tablename__ = 'users'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    email = Column(String(100), nullable=False)\n"
    ),
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv in a test
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def commit_all(root: Path, message: str = "state") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def edit_file(root: Path, rel: str, old: str, new: str) -> None:
    target = root / rel
    text = target.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    target.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


@dataclass
class Workspace:
    file: Path
    roots: dict[str, Path]

    def load(self) -> tuple[WorkspaceGraph, dict[str, ProjectRead]]:
        return load_workspace(self.file)


def make_workspace(
    tmp: Path, projects: dict[str, Files] | None = None, *, git_projects: tuple[str, ...] = ("api",)
) -> Workspace:
    """Write the projects, `git init` and commit the listed ones, add all to a workspace file."""
    spec = projects if projects is not None else {"api": API, "web": WEB}
    roots = {name: write_project(tmp / name, files) for name, files in spec.items()}
    for name in git_projects:
        git(roots[name], "init", "-q")
        commit_all(roots[name], "base")
    path = tmp / "ws.sqlite3"
    with WorkspaceStore.create(path) as store:
        for name, root in roots.items():
            store.add_project(name, ProjectKind.LOCAL, str(root))
    return Workspace(path, roots)
