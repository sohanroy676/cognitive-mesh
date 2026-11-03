"""Groq client (ADR-0013). Every failure becomes a ProviderError with fixed text only.

Prompt text, reply text, response bodies, exception text and the API key are never put in a message.
The endpoint is a constant, so the key can only be sent to Groq over HTTPS.
"""

import json
from typing import Any

from pydantic import SecretStr

from cognitive_mesh.providers.base import ModelRequest, ModelResponse
from cognitive_mesh.providers.errors import status_error, transport_error
from cognitive_mesh.providers.http import HttpTransport, StdlibTransport, TransportError
from cognitive_mesh.providers.openai_wire import parse_chat_reply

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_COMPLETION_TOKENS = 16_384  # the documented limit of the default Qwen model
_BODY_SLACK = 64 * 1024
_ESCAPE_FACTOR = 6  # worst case: every output char is a \uXXXX escape
_REASONING_MARKERS = ("<think>", "</think>")


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
        text, prompt_tokens, completion_tokens = parse_chat_reply(self.provider, reply.body)
        return ModelResponse(
            provider=self.provider,
            model=self.model,
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
