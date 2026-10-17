import json
from collections.abc import Mapping

import pytest

from cognitive_mesh.providers import ModelRequest, ProviderError, ProviderErrorKind
from cognitive_mesh.providers.http import HttpReply, TransportError, TransportErrorKind
from cognitive_mesh.providers.ollama import OllamaClient

CANARY_PROMPT = "CANARY-PROMPT-7731"
CANARY_BODY = "CANARY-BODY-5520"
CANARY_REPLY = "CANARY-REPLY-9042"


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


def _ok(content: str = '{"summary": "x"}', **extra: object) -> HttpReply:
    data = {"message": {"role": "assistant", "content": content}, "done": True, **extra}
    return HttpReply(200, json.dumps(data).encode())


def _request(fmt: str = "json") -> ModelRequest:
    return ModelRequest(
        system="sys",
        prompt=CANARY_PROMPT,
        timeout_s=7.5,
        max_output_chars=1000,
        response_format=fmt,
    )


def _client(reply: HttpReply | TransportErrorKind) -> tuple[OllamaClient, FakeTransport]:
    transport = FakeTransport(reply)
    return OllamaClient("http://host:1234/", "llama3.2", transport), transport


def test_success_json_sends_the_documented_request() -> None:
    client, transport = _client(_ok(CANARY_REPLY))
    response = client.complete(_request())
    assert (response.provider, response.model, response.text) == (
        "ollama",
        "llama3.2",
        CANARY_REPLY,
    )
    call = transport.calls[0]
    assert call["url"] == "http://host:1234/api/chat"
    assert call["timeout_s"] == 7.5
    assert call["body"] == {
        "model": "llama3.2",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": CANARY_PROMPT},
        ],
        "stream": False,
        "format": "json",
    }
    assert call["max_body_bytes"] == 1000 * 6 + 64 * 1024


def test_text_format_omits_the_json_format_flag() -> None:
    client, transport = _client(_ok("plain"))
    assert client.complete(_request("text")).text == "plain"
    assert "format" not in transport.calls[0]["body"]  # type: ignore[operator]


def test_the_server_reported_model_name_is_not_trusted() -> None:
    client, _ = _client(_ok(model="something-else"))
    assert client.complete(_request()).model == "llama3.2"


K = ProviderErrorKind


@pytest.mark.parametrize(
    ("reply", "kind", "retryable"),
    [
        ("timeout", K.TIMEOUT, True),
        ("unavailable", K.UNAVAILABLE, True),
        ("too_large", K.BAD_RESPONSE, False),
        (HttpReply(429, b""), K.RATE_LIMITED, True),
        (HttpReply(500, b""), K.UNAVAILABLE, True),
        (HttpReply(503, b""), K.UNAVAILABLE, True),
        (HttpReply(401, b""), K.AUTH, False),
        (HttpReply(403, b""), K.AUTH, False),
        (HttpReply(404, b""), K.OTHER, False),
        (HttpReply(400, b""), K.OTHER, False),
        (HttpReply(301, b""), K.BAD_RESPONSE, False),
        (HttpReply(100, b""), K.BAD_RESPONSE, False),
        (HttpReply(204, b""), K.BAD_RESPONSE, False),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_failures_map_to_the_documented_kind(
    reply: HttpReply | TransportErrorKind, kind: ProviderErrorKind, retryable: bool
) -> None:
    client, _ = _client(reply)
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    assert exc.value.kind is kind
    assert exc.value.retryable is retryable


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'"text"',
        b"{}",
        b'{"message": "x", "done": true}',
        b'{"message": {"content": 5}, "done": true}',
        b'{"message": {"content": "x"}}',
        b'{"message": {"content": "x"}, "done": false}',
        b'{"message": {"content": "x"}, "done": "true"}',
        b'{"message": {"content": "x"}, "done": true, "done_reason": "length"}',
        b"[" * 100_000,
    ],
    ids=range(12),
)
def test_malformed_replies_are_bad_response_and_not_retryable(body: bytes) -> None:
    client, _ = _client(HttpReply(200, body))
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    assert exc.value.kind is K.BAD_RESPONSE
    assert not exc.value.retryable


@pytest.mark.parametrize(
    "reply",
    [
        HttpReply(500, json.dumps({"error": CANARY_BODY, "echo": CANARY_PROMPT}).encode()),
        HttpReply(404, CANARY_BODY.encode()),
        HttpReply(200, CANARY_BODY.encode()),
        HttpReply(200, json.dumps({"message": {"content": CANARY_BODY}}).encode()),
        HttpReply(
            200,
            json.dumps(
                {"message": {"content": CANARY_BODY}, "done": True, "done_reason": "length"}
            ).encode(),
        ),
        "timeout",
        "unavailable",
    ],
    ids=range(7),
)
def test_error_text_never_contains_prompt_or_body_text(
    reply: HttpReply | TransportErrorKind,
) -> None:
    client, _ = _client(reply)
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    text = f"{exc.value} {exc.value!r} {exc.value.args}"
    assert CANARY_BODY not in text
    assert CANARY_PROMPT not in text
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


def test_an_empty_reply_text_is_passed_through_for_the_agent_to_reject() -> None:
    client, _ = _client(_ok(""))
    assert client.complete(_request()).text == ""
