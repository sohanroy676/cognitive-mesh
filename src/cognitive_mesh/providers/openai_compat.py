"""Generic OpenAI-compatible chat client (ADR-0038): Gemini, OpenRouter, Mistral, local servers.

Every failure becomes a ProviderError with fixed text only (see `providers/errors.py`). Prompt text,
reply text, response bodies, exception text and the API key are never put in a message. Unlike
`GroqClient` the endpoint is not a constant: the factory builds it from a catalog entry after
checking https, the opt-ins and the key variable.
"""

import json
from typing import Any, Literal

from pydantic import SecretStr

from cognitive_mesh.providers.base import ModelRequest, ModelResponse
from cognitive_mesh.providers.errors import status_error, transport_error
from cognitive_mesh.providers.http import HttpTransport, StdlibTransport, TransportError
from cognitive_mesh.providers.openai_wire import parse_chat_reply

DEFAULT_MAX_COMPLETION_TOKENS = 16_384
_BODY_SLACK = 64 * 1024
_ESCAPE_FACTOR = 6  # worst case: every output char is a \uXXXX escape

MaxTokensField = Literal["max_tokens", "max_completion_tokens"]


class OpenAICompatClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        api_key: SecretStr | None = None,
        *,
        json_mode: bool = True,
        max_tokens_field: MaxTokensField = "max_tokens",
        reasoning_effort: str = "",
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
        transport: HttpTransport | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._json_mode = json_mode
        self._max_tokens_field = max_tokens_field
        self._reasoning_effort = reasoning_effort
        self._max_completion_tokens = max_completion_tokens
        self._transport = transport or StdlibTransport()

    def __repr__(self) -> str:  # never shows the key
        return f"OpenAICompatClient(provider={self.provider!r}, model={self.model!r})"

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "stream": False,
            self._max_tokens_field: min(request.max_output_chars, self._max_completion_tokens),
        }
        if request.response_format == "json" and self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        # Built per call and never stored, logged or put in an error.
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        try:
            reply = self._transport.post_json(
                self._url,
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
