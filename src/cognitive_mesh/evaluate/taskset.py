"""The task-set file (ADR-0043): written by a person, read as data, never executed as code.

A task set names a workspace and a project, and lists small tasks, each with the files the coder
may see and the argv of the check that decides whether the task passed. The check is run only with
`--allow-unisolated-checks`, labelled not isolated (ADR-0042). Everything here is validated before
anything runs.
"""

import hashlib
import tomllib
from pathlib import Path, PurePosixPath
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cognitive_mesh.agents.coder import MAX_FILES
from cognitive_mesh.change.diffparse import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_LINES,
    HARD_MAX_FILES,
    HARD_MAX_LINES,
)
from cognitive_mesh.evaluate.errors import EvalError

MAX_TASKS: Final = 20  # small scale; larger sets wait for the user (Phase F)
MAX_TASKSET_BYTES: Final = 200_000
MAX_CHECK_WORDS: Final = 50
MAX_REQUEST_CHARS: Final = 2000
_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskSpec(_Strict):
    id: str = Field(pattern=_ID)
    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    files: tuple[str, ...] = Field(default=(), max_length=MAX_FILES)
    check: tuple[str, ...] = Field(min_length=1, max_length=MAX_CHECK_WORDS)
    check_timeout_s: int = Field(default=300, ge=1, le=3600)
    max_files: int = Field(default=DEFAULT_MAX_FILES, ge=1, le=HARD_MAX_FILES)
    max_lines: int = Field(default=DEFAULT_MAX_LINES, ge=1, le=HARD_MAX_LINES)

    @field_validator("request")
    @classmethod
    def _printable_request(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the request is blank")
        if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in value):
            raise ValueError("the request contains control characters")
        return value

    @field_validator("files")
    @classmethod
    def _relative_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for path in value:
            pure = PurePosixPath(path)
            if (
                not path
                or "\\" in path
                or pure.is_absolute()
                or ".." in pure.parts
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or len(path) > 300
            ):
                raise ValueError("a file path must be relative, inside the project, with '/'")
        return value

    @field_validator("check")
    @classmethod
    def _plain_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not word or "\x00" in word or len(word) > 500 for word in value):
            raise ValueError("a check word is empty, too long or contains NUL")
        return value


class TaskSetHeader(_Strict):
    name: str = Field(pattern=_ID)
    workspace: str = Field(min_length=1, max_length=500)
    project: str = Field(min_length=1, max_length=200)
    root: str = Field(min_length=1, max_length=500)


class _TaskSetFile(_Strict):
    set: TaskSetHeader
    task: tuple[TaskSpec, ...] = Field(min_length=1, max_length=MAX_TASKS)


class TaskSet(_Strict):
    name: str
    workspace: Path
    project: str
    root: Path
    tasks: tuple[TaskSpec, ...]
    sha256: str


def load_taskset(path: Path) -> TaskSet:
    """Read and validate a task-set file. Raises `EvalError` with a fixed message."""
    try:
        size = path.stat().st_size
    except OSError:
        raise EvalError("cannot read the task set file") from None
    if size > MAX_TASKSET_BYTES:
        raise EvalError("the task set file is too large")
    try:
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise EvalError("the task set file is not valid TOML") from None
    try:
        parsed = _TaskSetFile.model_validate(data)
    except ValidationError as exc:
        # Locations only: an error's input text can hold a request or a check word.
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise EvalError(f"the task set is invalid at: {', '.join(where)}") from None
    ids = [t.id for t in parsed.task]
    if len(set(ids)) != len(ids):
        raise EvalError("task ids must be unique")
    base = path.resolve().parent

    def resolve(text: str) -> Path:
        given = Path(text)
        return given if given.is_absolute() else (base / given).resolve()

    return TaskSet(
        name=parsed.set.name,
        workspace=resolve(parsed.set.workspace),
        project=parsed.set.project,
        root=resolve(parsed.set.root),
        tasks=parsed.task,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
