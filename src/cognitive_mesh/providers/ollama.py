"""Ollama client (ADR-0012). Every failure becomes a ProviderError with fixed text only.

Prompt text, reply text, response bodies and exception text are never put in a message.
"""

import json
from typing import Any

from cognitive_mesh.providers.base import ModelRequest, ModelResponse, ProviderError
from cognitive_mesh.providers.errors import bad_reply, status_error, transport_error
from cognitive_mesh.providers.http import HttpTransport, StdlibTransport, TransportError

_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
_BODY_SLACK = 64 * 1024
_ESCAPE_FACTOR = 6  # worst case: every output char is a \uXXXX escape


def _bad(reason: str) -> ProviderError:
    return bad_reply("ollama", reason)


def _parse_reply(body: bytes) -> str:
    try:
        data: Any = json.loads(body)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise _bad("not JSON") from None
    if not isinstance(data, dict):
        raise _bad("not an object")
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise _bad("no message content")
    if data.get("done") is not True:
        raise _bad("not finished")
    if data.get("done_reason") == "length":
        raise _bad("truncated")
    return content


class OllamaClient:
    provider = "ollama"

    def __init__(self, base_url: str, model: str, transport: HttpTransport | None = None) -> None:
        self._url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self._transport = transport or StdlibTransport()

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "stream": False,
        }
        if request.response_format == "json":
            payload["format"] = "json"
        try:
            reply = self._transport.post_json(
                self._url,
                json.dumps(payload).encode("utf-8"),
                _HEADERS,
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
