"""Reply parsing shared by every OpenAI-style chat client (ADR-0013, ADR-0038).

Failures are `ProviderError`s with fixed text only: reply text and bodies never enter a message.
"""

import json
from typing import Any

from cognitive_mesh.providers.errors import bad_reply

_REASONING_MARKERS = ("<think>", "</think>")


def token_count(value: object) -> int | None:
    """A reported token count, or None (unknown) for anything that is not a non-negative int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_chat_reply(provider: str, body: bytes) -> tuple[str, int | None, int | None]:
    """(content, prompt_tokens, completion_tokens) of a finished chat completion, or ProviderError.

    Reasoning text is never stripped and trusted: a reply that still carries it is rejected.
    """
    try:
        data: Any = json.loads(body)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise bad_reply(provider, "not JSON") from None
    if not isinstance(data, dict):
        raise bad_reply(provider, "not an object")
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise bad_reply(provider, "no choice")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise bad_reply(provider, "no message content")
    finish = choice.get("finish_reason")
    if finish == "length":
        raise bad_reply(provider, "truncated")
    if finish != "stop":
        raise bad_reply(provider, "not finished")
    if any(marker in content for marker in _REASONING_MARKERS):
        raise bad_reply(provider, "reasoning text in reply")
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return content, None, None
    return (
        content,
        token_count(usage.get("prompt_tokens")),
        token_count(usage.get("completion_tokens")),
    )
