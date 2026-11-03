import json

import pytest
from pydantic import SecretStr

from cognitive_mesh.providers import (
    GroqClient,
    ModelClient,
    OllamaClient,
    OpenAICompatClient,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.http import HttpReply, TransportErrorKind
from providers_support import (
    CANARY_BODY,
    CANARY_PROMPT,
    CANARY_REPLY,
    FakeTransport,
    fake_key,
    openai_ok,
    request,
)

K = ProviderErrorKind
BASE = "https://llm.example.test/v1beta/openai/"


def _client(
    reply: HttpReply | TransportErrorKind, key: str | None = None, **options: object
) -> tuple[OpenAICompatClient, FakeTransport]:
    transport = FakeTransport(reply)
    secret = SecretStr(key) if key is not None else None
    client = OpenAICompatClient(
        "gem",
        BASE,
        "gemini-x",
        secret,
        transport=transport,
        **options,  # type: ignore[arg-type]
    )
    return client, transport


def test_request_shape_url_and_auth_header() -> None:
    key = fake_key()
    client, transport = _client(openai_ok(CANARY_REPLY), key)
    response = client.complete(request())
    assert (response.provider, response.model, response.text) == ("gem", "gemini-x", CANARY_REPLY)
    call = transport.calls[0]
    assert call["url"] == "https://llm.example.test/v1beta/openai/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {key}"  # type: ignore[index]
    body = call["body"]
    assert body["model"] == "gemini-x"  # type: ignore[index]
    assert body["response_format"] == {"type": "json_object"}  # type: ignore[index]
    assert body["max_tokens"] == 1000  # type: ignore[index]
    assert "max_completion_tokens" not in body  # type: ignore[operator]
    assert call["timeout_s"] == 7.5


def test_no_key_sends_no_authorization_header() -> None:
    client, transport = _client(openai_ok())
    client.complete(request())
    assert "Authorization" not in transport.calls[0]["headers"]  # type: ignore[operator]


def test_options_change_the_wire_format() -> None:
    client, transport = _client(
        openai_ok(),
        json_mode=False,
        max_tokens_field="max_completion_tokens",
        reasoning_effort="low",
    )
    client.complete(request(max_chars=10**6))
    body = transport.calls[0]["body"]
    assert "response_format" not in body  # type: ignore[operator]
    assert body["max_completion_tokens"] == 16_384  # type: ignore[index]
    assert body["reasoning_effort"] == "low"  # type: ignore[index]


def test_text_format_omits_json_mode() -> None:
    client, transport = _client(openai_ok())
    client.complete(request("text"))
    assert "response_format" not in transport.calls[0]["body"]  # type: ignore[operator]


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 11, "completion_tokens": 5}, (11, 5)),
        (None, (None, None)),
        ({}, (None, None)),
        ({"prompt_tokens": 11}, (11, None)),
        ({"prompt_tokens": -1, "completion_tokens": 2.5}, (None, None)),
        ({"prompt_tokens": True, "completion_tokens": "7"}, (None, None)),
        ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0)),
        ("many", (None, None)),
    ],
)
def test_missing_or_garbled_usage_is_unknown_never_zero(
    usage: object, expected: tuple[int | None, int | None]
) -> None:
    client, _ = _client(openai_ok(usage=usage))
    response = client.complete(request())
    assert (response.prompt_tokens, response.completion_tokens) == expected


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, K.RATE_LIMITED),
        (498, K.RATE_LIMITED),
        (500, K.UNAVAILABLE),
        (503, K.UNAVAILABLE),
        (401, K.AUTH),
        (403, K.AUTH),
        (404, K.OTHER),
        (400, K.OTHER),
        (302, K.BAD_RESPONSE),
    ],
)
def test_failures_map_to_the_documented_kind(status: int, kind: ProviderErrorKind) -> None:
    client, _ = _client(HttpReply(status, CANARY_BODY.encode()))
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    assert info.value.kind is kind


def test_a_quota_shaped_429_is_rate_limited_and_the_body_is_not_read() -> None:
    body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": CANARY_BODY}})
    client, _ = _client(HttpReply(429, body.encode()))
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    assert info.value.kind is K.RATE_LIMITED and info.value.retryable
    assert CANARY_BODY not in str(info.value)


def test_a_retired_model_404_names_the_model_check() -> None:
    client, _ = _client(HttpReply(404, b"{}"))
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    assert "retired model" in str(info.value) and not info.value.retryable


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("timeout", K.TIMEOUT), ("unavailable", K.UNAVAILABLE), ("too_large", K.BAD_RESPONSE)],
)
def test_transport_failures(kind: TransportErrorKind, expected: ProviderErrorKind) -> None:
    client, _ = _client(kind)
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    assert info.value.kind is expected


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"choices": []}',
        b'{"choices": [{"message": {"content": 5}, "finish_reason": "stop"}]}',
        b'{"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}',
        b'{"choices": [{"message": {"content": "x"}}]}',
        b'{"choices": [{"message": {"content": "<think>x</think>{}"}, "finish_reason": "stop"}]}',
    ],
)
def test_malformed_replies_are_bad_response(body: bytes) -> None:
    client, _ = _client(HttpReply(200, body))
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    assert info.value.kind is K.BAD_RESPONSE and not info.value.retryable


def test_the_key_never_appears_in_errors_or_repr() -> None:
    key = fake_key()
    client, _ = _client(HttpReply(401, CANARY_BODY.encode()), key)
    with pytest.raises(ProviderError) as info:
        client.complete(request())
    for text in (str(info.value), repr(client), repr(vars(client))):
        assert key not in text
        assert CANARY_PROMPT not in text and CANARY_BODY not in text


def test_all_three_clients_map_the_same_failures_the_same_way() -> None:
    for status in (429, 500, 401, 404, 400):
        kinds = []
        for build in (
            lambda t: OpenAICompatClient("x", BASE, "m", transport=t),
            lambda t: GroqClient(SecretStr(fake_key("gsk_")), "m", "", t),
            lambda t: OllamaClient("http://localhost:11434", "m", t),
        ):
            client: ModelClient = build(FakeTransport(HttpReply(status, b"{}")))
            with pytest.raises(ProviderError) as info:
                client.complete(request())
            kinds.append(info.value.kind)
        assert len(set(kinds)) == 1, status


def test_groq_and_openai_compat_read_the_same_reply_the_same_way() -> None:
    reply = openai_ok("hello", usage={"prompt_tokens": 3, "completion_tokens": 4})
    groq = GroqClient(SecretStr(fake_key("gsk_")), "m", "", FakeTransport(reply))
    compat = OpenAICompatClient("x", BASE, "m", transport=FakeTransport(reply))
    a, b = groq.complete(request()), compat.complete(request())
    assert (a.text, a.prompt_tokens, a.completion_tokens) == (
        b.text,
        b.prompt_tokens,
        b.completion_tokens,
    )
    assert (b.prompt_tokens, b.completion_tokens) == (3, 4)


def test_ollama_reports_token_counts_or_unknown() -> None:
    def ok(**extra: object) -> HttpReply:
        data = {"message": {"content": "hi"}, "done": True, **extra}
        return HttpReply(200, json.dumps(data).encode())

    known = OllamaClient(
        "http://localhost:11434", "m", FakeTransport(ok(prompt_eval_count=9, eval_count=2))
    )
    unknown = OllamaClient("http://localhost:11434", "m", FakeTransport(ok()))
    r1, r2 = known.complete(request()), unknown.complete(request())
    assert (r1.prompt_tokens, r1.completion_tokens) == (9, 2)
    assert (r2.prompt_tokens, r2.completion_tokens) == (None, None)
