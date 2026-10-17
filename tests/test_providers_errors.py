"""Shared status mapping (ADR-0012, ADR-0013 amendment): the 404 hint."""

import pytest

from cognitive_mesh.providers.base import ProviderErrorKind
from cognitive_mesh.providers.errors import status_error


@pytest.mark.parametrize("provider", ["groq", "ollama"])
def test_a_404_points_at_the_model_name_without_changing_the_kind(provider: str) -> None:
    err = status_error(provider, 404)
    assert err is not None
    assert err.kind is ProviderErrorKind.OTHER
    assert not err.retryable
    assert "HTTP 404" in str(err)
    assert "model" in str(err)


@pytest.mark.parametrize("status", [400, 422, 410])
def test_other_client_errors_get_no_model_hint(status: int) -> None:
    err = status_error("groq", status)
    assert err is not None
    assert "model" not in str(err)
