"""Record the model replies of a real chat turn, and play them back for a demo.

`serve --capture DIR` writes, for every turn, the replies the models gave (and the errors they
raised). `serve --replay FILE` starts the app with no model, key or internet: the models are
replaced by those recorded replies, everything else is the real application. The plan is made, the
files are read, the diff is applied in a git worktree, the contract check runs, a decision and a
trace are stored and the approval buttons work, exactly as in a live run. Only the text a model
would have written comes from the file.

A replayed request must be one of the recorded requests (the replies would not fit another one).
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 - runs git with fixed arguments
import sys
import threading
import time
import tomllib
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cognitive_mesh.providers import ModelRequest, ModelResponse, ProviderError, ProviderErrorKind

if TYPE_CHECKING:
    from cognitive_mesh.providers import ModelClient
    from cognitive_mesh.serve.app_api import AppApi

MAX_RECORDING_BYTES: Final = 4 * 1024 * 1024
MAX_REPLIES: Final = 200
TURN_WAIT_S: Final = 300.0

# The first words of each agent's system prompt tell which agent is asking.
_ROLES: Final = (
    ("You are the conductor", "conductor"),
    ("You propose one small change", "coder"),
    ("You explain one source file", "explainer"),
    ("You answer one question", "answerer"),
    ("You read files of other projects", "reader"),
)


class ReplayError(Exception):
    """The recording cannot be used; the message is fixed text."""


def classify(request: ModelRequest) -> tuple[str, str]:
    """(agent role, key) of a model request. The key is the file path for the explainer."""
    for start, role in _ROLES:
        if request.system.startswith(start):
            key = ""
            if role == "explainer":
                try:
                    key = str(json.loads(request.prompt).get("path", ""))
                except (ValueError, AttributeError):
                    key = ""
            return role, key
    return "other", ""


# -- the recording ------------------------------------------------------------------------------


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    sample: str | None = Field(default=None, max_length=100)  # a folder next to the recording
    checkout: bool = False  # this checkout of Cognitive Mesh itself
    check: list[str] | None = Field(default=None, max_length=20)  # the declared check command


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=40)
    key: str = Field(default="", max_length=500)
    entry: str = Field(default="", max_length=100)
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=200_000)
    error: str | None = Field(default=None, max_length=40)
    # What the real call took and used, shown in the trace of a replayed chat (None = unknown).
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    project: ProjectSpec
    request: str = Field(min_length=1, max_length=4000)
    mode: str = Field(pattern="^(ask|change)$")
    conductor: str = Field(default="groq", max_length=100)
    replies: list[Reply] = Field(max_length=MAX_REPLIES)
    # Set on the recording of a revision: the person's note. Its `request` is then the original
    # request plus " (revise: NOTE)", which is the text the Revise button sends.
    revise_note: str | None = Field(default=None, max_length=1000)
    source: Path | None = Field(default=None, exclude=True)


def load_recording(path: Path) -> Recording:
    try:
        if path.stat().st_size > MAX_RECORDING_BYTES:
            raise ReplayError("the recording is larger than 4 MB")
        recording = Recording.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        raise ReplayError("the recording cannot be read") from None
    except (ValueError, ValidationError):
        raise ReplayError("the recording is not a valid recording file") from None
    return recording.model_copy(update={"source": path})


# -- playing it back ----------------------------------------------------------------------------


class ReplayClient:
    """A model client that answers from the recorded replies of the selected recording."""

    def __init__(self, models: ReplayModels, provider: str, model: str) -> None:
        self._models = models
        self.provider = provider
        self.model = model
        self.replayed_latency_ms: int | None = None  # read by RecordingClient

    def complete(self, request: ModelRequest) -> ModelResponse:
        role, key = classify(request)
        reply = self._models.take(role, key)
        self.replayed_latency_ms = reply.latency_ms if reply is not None else None
        if reply is None:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, "no recorded reply left")
        if reply.error is not None:
            try:
                kind = ProviderErrorKind(reply.error)
            except ValueError:
                kind = ProviderErrorKind.OTHER
            raise ProviderError(kind, "recorded error")
        return ModelResponse(
            provider=reply.provider or self.provider,
            model=reply.model or self.model,
            text=reply.text or "",
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
        )


class ReplayModels:
    """The model side of a demo: one client per catalog entry, all answering from the recording
    chosen by `select`. Used as `ServeConfig.client_factory`."""

    def __init__(self, recordings: list[Recording]) -> None:
        self.recordings = recordings
        self._by_request = {r.request.strip(): r for r in recordings}
        self._revisions: dict[str, Recording] = {}  # original request -> its recorded revision
        for rec in recordings:
            if rec.revise_note:
                tail = f" (revise: {rec.revise_note})"
                if rec.request.endswith(tail):
                    self._revisions[rec.request.removesuffix(tail).strip()] = rec
        self._labels: dict[str, tuple[str, str]] = {}
        for rec in recordings:
            for reply in rec.replies:
                if reply.entry and reply.provider and reply.model:
                    self._labels.setdefault(reply.entry, (reply.provider, reply.model))
        self._queues: dict[tuple[str, str], deque[Reply]] = {}
        self._lock = threading.Lock()

    def __call__(self, entry_id: str) -> ModelClient:
        provider, model = self._labels.get(entry_id, ("replay", entry_id))
        return ReplayClient(self, provider, model)

    def notes(self) -> dict[str, str]:
        """For each request that has a recorded revision, the note that was written for it."""
        return {base: rec.revise_note or "" for base, rec in self._revisions.items()}

    def _resolve(self, text: str) -> Recording | None:
        found = self._by_request.get(text.strip())
        if found is not None:
            return found
        base, marker, _ = text.strip().partition(" (revise: ")
        if marker and text.strip().endswith(")"):
            return self._revisions.get(base.strip())
        return None

    def check(self, text: str) -> str | None:
        """Why a request cannot be answered in demo mode, or None."""
        if self._resolve(text) is not None:
            return None
        if " (revise: " in text:
            return (
                "demo mode has no recorded revision for this chat; the Revise button needs a model"
            )
        return (
            "demo mode answers only the recorded requests, because the models' replies are "
            "played back from a file; use one of the previous chats or start without --replay"
        )

    def select(self, text: str) -> None:
        recording = self._resolve(text)
        queues: dict[tuple[str, str], deque[Reply]] = {}
        for reply in recording.replies if recording else []:
            queues.setdefault((reply.role, reply.key), deque()).append(reply)
        with self._lock:
            self._queues = queues

    def take(self, role: str, key: str) -> Reply | None:
        with self._lock:
            queue = self._queues.get((role, key))
            return queue.popleft() if queue else None


def write_replay_catalog(source: Path, home: Path) -> Path:
    """The model catalog of a demo: the entries of `source` with the same ids and roles, but all
    local, so that none needs a key or a remote-content opt-in (nothing is ever sent anywhere)."""
    try:
        entries = tomllib.loads(source.read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        raise ReplayError("the model catalog to copy the entries from cannot be read") from None
    lines = ["# written by `serve --replay`: the entries of the real catalog, all local\n"]
    for entry_id, entry in entries.items():
        roles = ", ".join(json.dumps(r) for r in entry["roles"])
        lines.append(
            f"\n[models.{entry_id}]\n"
            'wire = "ollama"\nbase_url = "http://localhost:11434"\n'
            f"model = {json.dumps(entry['model'])}\n"
            f"context_tokens = {int(entry['context_tokens'])}\n"
            f'tier = "free"\ndata_use = "no_training"\nroles = [{roles}]\n'
        )
    home.mkdir(parents=True, exist_ok=True)
    target = home / "replay-catalog.toml"
    target.write_text("".join(lines), encoding="utf-8")
    return target


# -- recording -----------------------------------------------------------------------------------


class CaptureClient:
    """Passes every call through and notes the reply (or the error) for `serve --capture`."""

    def __init__(self, inner: ModelClient, sink: list[dict[str, Any]], entry: str) -> None:
        self._inner = inner
        self._sink = sink
        self._entry = entry
        self.provider = inner.provider
        self.model = inner.model

    def complete(self, request: ModelRequest) -> ModelResponse:
        role, key = classify(request)
        note: dict[str, Any] = {"role": role, "key": key, "entry": self._entry}
        start = time.monotonic()
        try:
            response = self._inner.complete(request)
        except ProviderError as exc:
            note["latency_ms"] = round((time.monotonic() - start) * 1000)
            self._sink.append({**note, "error": exc.kind.value})
            raise
        note["latency_ms"] = round((time.monotonic() - start) * 1000)
        self._sink.append(
            {
                **note,
                "provider": response.provider,
                "model": response.model,
                "text": response.text,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        )
        return response


# -- starting the recorded chats -----------------------------------------------------------------


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [  # noqa: S607
            "git",
            "-c",
            "user.name=Cognitive Mesh demo",
            "-c",
            "user.email=demo@example.invalid",
            *args,
        ],
        cwd=folder,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def make_git_sample(source: Path, dest: Path, origin: str | None = None) -> None:
    """Copy a sample project to `dest` and make it a git repository with one commit on `main`."""
    if dest.exists():
        raise ReplayError(f"{dest} already exists")
    shutil.copytree(source, dest)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "Initial commit")
    if origin is not None:
        git(dest, "remote", "add", "origin", origin)


def _project_folder(recording: Recording, home: Path) -> Path:
    spec = recording.project
    if spec.sample:
        base = recording.source.parent if recording.source else Path.cwd()
        dest = home / "demo-projects" / spec.name
        if not dest.exists():
            make_git_sample(base / spec.sample, dest)
        return dest
    if spec.checkout:
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
                return parent
        raise ReplayError("this Cognitive Mesh checkout is not a git repository")
    raise ReplayError("the recording names no project folder")


def _finished(api: AppApi, recording: Recording) -> bool:
    for session in api.chat.sessions():
        if session["project"] != recording.project.name:
            continue
        for turn_id in api.chat.session_turns(session["id"]):
            turn = api.chat.turn(turn_id)
            if (
                turn is not None
                and turn["text"] == recording.request
                and turn["status"] in ("done", "rejected")
            ):
                return True
    return False


def run_recordings(api: AppApi, recordings: list[Recording], home: Path) -> None:
    """Make each recorded chat a real, finished-or-waiting turn, one after the other."""
    for recording in recordings:
        if recording.revise_note:
            continue  # started by the Revise button, with the person's note
        try:
            if _finished(api, recording):
                continue
            folder = _project_folder(recording, home)
            api.ensure_project(recording.project.name, folder, recording.project.check)
            print(f"replay: {recording.title}", file=sys.stderr, flush=True)
            api.start_chat(recording.project.name, recording.request, recording.mode)
            deadline = time.monotonic() + TURN_WAIT_S
            while api.chat.running_turn() is not None and time.monotonic() < deadline:
                time.sleep(0.2)
        except Exception as exc:  # a broken recording must not stop the others or the server
            print(f"replay: {recording.title}: {type(exc).__name__}: {exc}", file=sys.stderr)
