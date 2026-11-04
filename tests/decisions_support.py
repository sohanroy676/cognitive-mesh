"""Helpers for decision tests: a small application, a trace database, and record shortcuts."""

import subprocess
from pathlib import Path

from cognitive_mesh.decisions.models import Decision, MadeBy
from cognitive_mesh.decisions.record import record_decision
from cognitive_mesh.traces import TraceStore
from memory_support import add_run, success

APP_FILES = {
    "a.py": "import b\n\nVALUE = 1\n",
    "b.py": "def helper():\n    return 2\n",
    "README.md": "# demo\n",
}


def make_app(root: Path, files: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in (files or APP_FILES).items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def make_db(tmp: Path) -> Path:
    """A trace database with one finished run holding one step (`run-1` / `s1`)."""
    db = tmp / "traces.sqlite3"
    add_run(
        db,
        "run-1",
        [
            (
                "s1",
                "dependencies_of",
                {"node_id": "module:a.py"},
                success("run-1", "s1", "dependencies_of", {}),
            )
        ],
    )
    return db


def git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603, S607 - test helper with fixed arguments
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def person(store: TraceStore, **overrides: object) -> Decision:
    """Record a person's decision with sensible defaults; `overrides` replace any argument."""
    args: dict[str, object] = {
        "summary": "Keep the helper in b.py",
        "options": ["keep it", "inline it"],
        "chosen": "keep it",
        "reasoning": "Two modules use it.",
        "made_by": MadeBy.PERSON,
        "recorder": "alice",
        "app_id": "demo",
    }
    args.update(overrides)
    decision, _ = record_decision(store, **args)  # type: ignore[arg-type]
    return decision
