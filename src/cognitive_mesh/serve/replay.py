"""A recorded chat turn that the app shows without any model, key or internet (demo).

`serve --replay FILE` puts the recording into the chat file as a finished turn. Its approval
buttons stay off (no approval is opened), and a `replay` event labels it as a recording, so it
can never be mistaken for a live result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cognitive_mesh.serve.chat_store import ChatStore

MAX_RECORDING_BYTES: Final = 2 * 1024 * 1024
MAX_EVENTS: Final = 200
NOTICE: Final = (
    "This turn was run once with real models and is shown from a saved file. Nothing here "
    "calls a model now, and the approval buttons are off."
)


class ReplayError(Exception):
    """The recording cannot be used; the message is fixed text."""


class RecordedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=40)
    data: dict[str, Any]


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    project: str = Field(min_length=1, max_length=200)
    request: str = Field(min_length=1, max_length=4000)
    mode: str = Field(pattern="^(ask|change)$")
    memory: bool = True
    events: list[RecordedEvent] = Field(max_length=MAX_EVENTS)


def load_recording(path: Path) -> Recording:
    try:
        if path.stat().st_size > MAX_RECORDING_BYTES:
            raise ReplayError("the recording is larger than 2 MB")
        return Recording.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        raise ReplayError("the recording cannot be read") from None
    except (ValueError, ValidationError):
        raise ReplayError("the recording is not a valid recording file") from None


def seed_replay(chat: ChatStore, recording: Recording) -> int:
    """Store the recording as one session with one finished turn. Returns the turn id.
    Running it again with the same recording adds nothing."""
    for session in chat.sessions():
        if session["project"] != recording.project:
            continue
        for turn_id in chat.session_turns(session["id"]):
            turn = chat.turn(turn_id)
            events = chat.events(turn_id)
            if (
                turn is not None
                and turn["text"] == recording.request
                and events
                and events[0]["kind"] == "replay"
            ):
                return turn_id
    session_id = chat.new_session(recording.project)
    turn_id = chat.begin_turn(
        session_id, recording.project, recording.request, recording.memory, recording.mode
    )
    chat.add_event(turn_id, "replay", {"message": NOTICE, "title": recording.title})
    for event in recording.events:
        chat.add_event(turn_id, event.kind, event.data)
    chat.add_event(turn_id, "done", {"kind": "replay"})
    chat.end_worker(turn_id)
    return turn_id
