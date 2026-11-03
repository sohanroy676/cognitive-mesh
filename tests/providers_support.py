"""Shared fakes for provider tests: a transport that records calls and returns a canned reply."""

import json
from collections.abc import Mapping

from cognitive_mesh.providers import ModelRequest
from cognitive_mesh.providers.http import HttpReply, TransportError, TransportErrorKind

CANARY_PROMPT = "CANARY-PROMPT-7731"
CANARY_BODY = "CANARY-BODY-5520"
CANARY_REPLY = "CANARY-REPLY-9042"


def fake_key(prefix: str = "ak_") -> str:
    """A key-shaped string built at run time so no scanner sees a literal."""
    return prefix + "CANARY" + "KEY0123456789"


class FakeTransport:
    def __init__(self, reply: HttpReply | TransportErrorKind) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpReply:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body),
                "headers": dict(headers),
                "timeout_s": timeout_s,
                "max_body_bytes": max_body_bytes,
            }
        )
        if isinstance(self.reply, str):
            raise TransportError(self.reply)
        return self.reply


def openai_ok(
    content: str = '{"summary": "x"}', *, finish: object = "stop", usage: object = None
) -> HttpReply:
    choice: dict[str, object] = {"message": {"role": "assistant", "content": content}}
    if finish is not None:
        choice["finish_reason"] = finish
    data: dict[str, object] = {"choices": [choice]}
    if usage is not None:
        data["usage"] = usage
    return HttpReply(200, json.dumps(data).encode())


def request(fmt: str = "json", max_chars: int = 1000) -> ModelRequest:
    return ModelRequest(
        system="sys",
        prompt=CANARY_PROMPT,
        timeout_s=7.5,
        max_output_chars=max_chars,
        response_format=fmt,  # type: ignore[arg-type]
    )
