import json
from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from cognitive_mesh.providers import (
    GroqClient,
    ModelRequest,
    OllamaClient,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.groq import GROQ_URL
from cognitive_mesh.providers.http import HttpReply, TransportError, TransportErrorKind

KEY = "gsk_" + "CANARY" + "KEY0123456789"  # assembled so no scanner sees a literal key
CANARY_PROMPT = "CANARY-PROMPT-7731"
CANARY_BODY = "CANARY-BODY-5520"
CANARY_REPLY = "CANARY-REPLY-9042"
K = ProviderErrorKind


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


def _ok(content: str = '{"summary": "x"}', finish: object = "stop", **message: object) -> HttpReply:
    choice = {"message": {"role": "assistant", "content": content, **message}}
    if finish is not None:
        choice["finish_reason"] = finish  # type: ignore[assignment]
    return HttpReply(200, json.dumps({"choices": [choice], "usage": {"total_tokens": 3}}).encode())


def _request(fmt: str = "json", max_chars: int = 1000) -> ModelRequest:
    return ModelRequest(
        system="sys",
        prompt=CANARY_PROMPT,
        timeout_s=7.5,
        max_output_chars=max_chars,
        response_format=fmt,
    )


def _client(
    reply: HttpReply | TransportErrorKind, effort: str = "none"
) -> tuple[GroqClient, FakeTransport]:
    transport = FakeTransport(reply)
    return GroqClient(SecretStr(KEY), "qwen/qwen3.8-27b", effort, transport), transport


def test_success_json_sends_the_documented_request() -> None:
    client, transport = _client(_ok(CANARY_REPLY))
    response = client.complete(_request())
    assert (response.provider, response.model, response.text) == (
        "groq",
        "qwen/qwen3.8-27b",
        CANARY_REPLY,
    )
    call = transport.calls[0]
    assert call["url"] == GROQ_URL == "https://api.groq.com/openai/v1/chat/completions"
    assert call["timeout_s"] == 7.5
    assert call["body"] == {
        "model": "qwen/qwen3.8-27b",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": CANARY_PROMPT},
        ],
        "stream": False,
        "max_completion_tokens": 1000,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "none",
    }
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"  # type: ignore[index]
    assert call["max_body_bytes"] == 1000 * 6 + 64 * 1024


def test_text_format_omits_json_mode_and_empty_effort_omits_reasoning() -> None:
    client, transport = _client(_ok("plain"), effort="")
    assert client.complete(_request("text")).text == "plain"
    body = transport.calls[0]["body"]
    assert "response_format" not in body  # type: ignore[operator]
    assert "reasoning_effort" not in body  # type: ignore[operator]


def test_the_completion_token_cap_is_bounded() -> None:
    client, transport = _client(_ok())
    client.complete(_request(max_chars=500_000))
    assert transport.calls[0]["body"]["max_completion_tokens"] == 16_384  # type: ignore[index]


def test_reasoning_and_usage_fields_are_ignored() -> None:
    client, _ = _client(_ok("answer", reasoning="CANARY-THOUGHTS"))
    assert client.complete(_request()).text == "answer"


FAILURES = [
    ("timeout", K.TIMEOUT, True),
    ("unavailable", K.UNAVAILABLE, True),
    ("too_large", K.BAD_RESPONSE, False),
    (HttpReply(429, b""), K.RATE_LIMITED, True),
    (HttpReply(498, b""), K.RATE_LIMITED, True),
    (HttpReply(500, b""), K.UNAVAILABLE, True),
    (HttpReply(502, b""), K.UNAVAILABLE, True),
    (HttpReply(503, b""), K.UNAVAILABLE, True),
    (HttpReply(401, b""), K.AUTH, False),
    (HttpReply(403, b""), K.AUTH, False),
    (HttpReply(400, b""), K.OTHER, False),
    (HttpReply(404, b""), K.OTHER, False),
    (HttpReply(413, b""), K.OTHER, False),
    (HttpReply(422, b""), K.OTHER, False),
    (HttpReply(499, b""), K.OTHER, False),
    (HttpReply(301, b""), K.BAD_RESPONSE, False),
    (HttpReply(100, b""), K.BAD_RESPONSE, False),
    (HttpReply(204, b""), K.BAD_RESPONSE, False),
]


def _id(v: object) -> str | None:
    if isinstance(v, str):
        return v
    return str(v.status) if isinstance(v, HttpReply) else None


@pytest.mark.parametrize(("reply", "kind", "retryable"), FAILURES, ids=_id)
def test_failures_map_to_the_documented_kind(
    reply: HttpReply | TransportErrorKind, kind: ProviderErrorKind, retryable: bool
) -> None:
    client, _ = _client(reply)
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    assert exc.value.kind is kind
    assert exc.value.retryable is retryable


@pytest.mark.parametrize(("reply", "kind", "retryable"), FAILURES, ids=_id)
def test_both_clients_map_the_same_failure_the_same_way(
    reply: HttpReply | TransportErrorKind, kind: ProviderErrorKind, retryable: bool
) -> None:
    groq, _ = _client(reply)
    ollama = OllamaClient("http://h:1", "m", FakeTransport(reply))
    outcomes = []
    for client in (groq, ollama):
        with pytest.raises(ProviderError) as exc:
            client.complete(_request())
        outcomes.append((exc.value.kind, exc.value.retryable))
    assert outcomes[0] == outcomes[1] == (kind, retryable)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'"text"',
        b"{}",
        b'{"choices": []}',
        b'{"choices": "x"}',
        b'{"choices": [5]}',
        b'{"choices": [{}]}',
        b'{"choices": [{"message": "x", "finish_reason": "stop"}]}',
        b'{"choices": [{"message": {"content": 5}, "finish_reason": "stop"}]}',
        b'{"choices": [{"message": {"content": "x"}}]}',
        b'{"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}',
        b'{"choices": [{"message": {"content": "x"}, "finish_reason": "tool_calls"}]}',
        b"[" * 100_000,
    ],
    ids=range(15),
)
def test_malformed_replies_are_bad_response_and_not_retryable(body: bytes) -> None:
    client, _ = _client(HttpReply(200, body))
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    assert exc.value.kind is K.BAD_RESPONSE
    assert not exc.value.retryable


@pytest.mark.parametrize(
    "content",
    ["<think>plan</think>{}", "{}</think>", 'prefix <think> {"a": 1}'],
    ids=range(3),
)
def test_reasoning_text_in_the_reply_is_rejected_not_stripped(content: str) -> None:
    client, _ = _client(_ok(content))
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    assert exc.value.kind is K.BAD_RESPONSE


@pytest.mark.parametrize(
    "reply",
    [
        HttpReply(
            500, json.dumps({"error": CANARY_BODY, "echo": CANARY_PROMPT, "k": KEY}).encode()
        ),
        HttpReply(401, f"bad key {KEY} {CANARY_BODY}".encode()),
        HttpReply(200, f"{CANARY_BODY} {KEY}".encode()),
        _ok(f"{CANARY_BODY} <think> {KEY}"),
        _ok(CANARY_BODY, finish="length"),
        "timeout",
        "unavailable",
    ],
    ids=range(7),
)
def test_error_text_never_contains_prompt_body_or_key(
    reply: HttpReply | TransportErrorKind,
) -> None:
    client, _ = _client(reply)
    with pytest.raises(ProviderError) as exc:
        client.complete(_request())
    text = f"{exc.value} {exc.value!r} {exc.value.args}"
    for secret in (CANARY_BODY, CANARY_PROMPT, KEY):
        assert secret not in text
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


def test_the_key_is_not_in_the_client_repr_or_attributes() -> None:
    client, _ = _client(_ok())
    assert KEY not in repr(client)
    assert KEY not in repr(vars(client))
    assert KEY not in str(client.__dict__.get("_api_key"))


def test_an_empty_reply_text_is_passed_through_for_the_agent_to_reject() -> None:
    client, _ = _client(_ok(""))
    assert client.complete(_request()).text == ""
