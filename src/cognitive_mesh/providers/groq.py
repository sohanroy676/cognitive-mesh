"""Groq client (ADR-0013). Every failure becomes a ProviderError with fixed text only.

Prompt text, reply text, response bodies, exception text and the API key are never put in a message.
The endpoint is a constant, so the key can only be sent to Groq over HTTPS.
"""

import json
from typing import Any

from pydantic import SecretStr

from cognitive_mesh.providers.base import ModelRequest, ModelResponse, ProviderError
from cognitive_mesh.providers.errors import bad_reply, status_error, transport_error
from cognitive_mesh.providers.http import HttpTransport, StdlibTransport, TransportError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_COMPLETION_TOKENS = 16_384  # the documented limit of the default Qwen model
_BODY_SLACK = 64 * 1024
_ESCAPE_FACTOR = 6  # worst case: every output char is a \uXXXX escape
_REASONING_MARKERS = ("<think>", "</think>")


def _bad(reason: str) -> ProviderError:
    return bad_reply("groq", reason)


def _parse_reply(body: bytes) -> str:
    try:
        data: Any = json.loads(body)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise _bad("not JSON") from None
    if not isinstance(data, dict):
        raise _bad("not an object")
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise _bad("no choice")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise _bad("no message content")
    finish = choice.get("finish_reason")
    if finish == "length":
        raise _bad("truncated")
    if finish != "stop":
        raise _bad("not finished")
    # Reasoning text is never stripped and trusted; a reply that still carries it is rejected.
    if any(marker in content for marker in _REASONING_MARKERS):
        raise _bad("reasoning text in reply")
    return content


class GroqClient:
    provider = "groq"

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        reasoning_effort: str = "",
        transport: HttpTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._reasoning_effort = reasoning_effort
        self._transport = transport or StdlibTransport()

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "stream": False,
            "max_completion_tokens": min(request.max_output_chars, MAX_COMPLETION_TOKENS),
        }
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        # Built per call and never stored, logged or put in an error.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
        }
        try:
            reply = self._transport.post_json(
                GROQ_URL,
                json.dumps(payload).encode("utf-8"),
                headers,
                timeout_s=request.timeout_s,
                max_body_bytes=request.max_output_chars * _ESCAPE_FACTOR + _BODY_SLACK,
            )
        except TransportError as exc:
            raise transport_error(self.provider, exc) from None
        error = status_error(self.provider, reply.status)
        if error is not None:
            raise error
        return ModelResponse(
            provider=self.provider, model=self.model, text=_parse_reply(reply.body)
        )
