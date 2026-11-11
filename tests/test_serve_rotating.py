"""The coder's models alternate between drafts and a failing one is replaced by the next."""

import pytest

from cognitive_mesh.providers import ModelRequest, ModelResponse, ProviderError, ProviderErrorKind
from cognitive_mesh.serve.app_api import RotatingClient

REQUEST = ModelRequest(system="s", prompt="p", timeout_s=5.0, max_output_chars=100)


class Fake:
    provider = "fake"

    def __init__(self, name: str, fail: bool = False) -> None:
        self.model = name
        self.fail = fail
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.fail:
            raise ProviderError(ProviderErrorKind.RATE_LIMITED, "limited")
        return ModelResponse(provider="fake", model=self.model, text="x")


def test_calls_alternate_between_clients() -> None:
    a, b = Fake("a"), Fake("b")
    client = RotatingClient([a, b])
    assert [client.complete(REQUEST).model for _ in range(4)] == ["a", "b", "a", "b"]


def test_a_failing_client_is_replaced_by_the_next() -> None:
    a, b = Fake("a", fail=True), Fake("b")
    client = RotatingClient([a, b])
    assert [client.complete(REQUEST).model for _ in range(3)] == ["b", "b", "b"]
    assert a.calls == 3  # asked first every time, since it never answered


def test_the_last_error_is_raised_when_all_fail() -> None:
    client = RotatingClient([Fake("a", fail=True), Fake("b", fail=True)])
    with pytest.raises(ProviderError):
        client.complete(REQUEST)
