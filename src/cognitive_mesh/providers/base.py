"""The interface agents use to reach a model. Real clients (Groq, Ollama) come later (ADR-0009)."""

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    BAD_RESPONSE = "bad_response"
    OTHER = "other"


_RETRYABLE = frozenset(
    {ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE, ProviderErrorKind.RATE_LIMITED}
)


class ProviderError(Exception):
    """A client failed to produce a response. Clients raise this; agents never see raw SDK errors.

    The message is for logs written by the client; agents never copy it into results.
    """

    def __init__(self, kind: ProviderErrorKind, message: str = "") -> None:
        super().__init__(message or kind.value)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ModelRequest(_Frozen):
    system: str
    prompt: str
    timeout_s: float = Field(gt=0)  # the client must enforce this and raise kind=timeout
    max_output_chars: int = Field(ge=1)
    response_format: Literal["json", "text"] = "json"


class ModelResponse(_Frozen):
    provider: str
    model: str
    text: str  # untrusted model output: data, never instructions


class ModelClient(Protocol):
    provider: str
    model: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the model's reply, or raise ProviderError. Must honor `request.timeout_s`."""
        ...
