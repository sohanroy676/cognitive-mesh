"""Per-call performance records (ADR-0038).

A `RecordingClient` wraps a real client and appends one `CallRecord` per `complete()` to a
`CallLog`. Records hold names, a latency, token counts and a result kind: never prompt text, reply
text, headers, URLs or error text. A token count the provider did not report is None (unknown),
never 0.
"""

import time
from collections.abc import Callable
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)


class CallResult(StrEnum):
    OK = "ok"
    TIMEOUT = ProviderErrorKind.TIMEOUT.value
    UNAVAILABLE = ProviderErrorKind.UNAVAILABLE.value
    RATE_LIMITED = ProviderErrorKind.RATE_LIMITED.value
    AUTH = ProviderErrorKind.AUTH.value
    BAD_RESPONSE = ProviderErrorKind.BAD_RESPONSE.value
    OTHER = ProviderErrorKind.OTHER.value
    CLIENT_ERROR = "client_error"  # the client raised something that is not a ProviderError


class CallRecord(BaseModel):
    """One model call, keyed to the agent attempt that made it: `agent_task_id` + `call_no`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_task_id: str = Field(min_length=1, max_length=300)
    step_id: str = Field(min_length=1, max_length=200)
    call_no: int = Field(ge=1)
    entry_id: str | None = None  # the catalog entry; None when COGMESH_PROVIDER=groq|ollama
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)  # the agent id that made the call
    task_type: str = Field(min_length=1, max_length=100)  # the capability being run
    latency_ms: int = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    result: CallResult

    @model_validator(mode="after")
    def _failures_have_no_tokens_to_trust(self) -> Self:
        # A call that produced no reply has no usage to report; refuse a record that claims one.
        if self.result is not CallResult.OK and (
            self.prompt_tokens is not None or self.completion_tokens is not None
        ):
            raise ValueError("only a successful call may carry token counts")
        return self


class CallLog:
    """Collects the records of one run. `begin_attempt` is the orchestrator's `on_attempt` hook."""

    def __init__(self) -> None:
        self._records: list[CallRecord] = []
        self._attempt: tuple[str, str, str, str] | None = None
        self._calls_in_attempt = 0
        self.dropped = 0  # records that failed validation; recording never changes a call

    @property
    def records(self) -> tuple[CallRecord, ...]:
        return tuple(self._records)

    def begin_attempt(
        self, agent_task_id: str, step_id: str, agent_id: str, capability: str
    ) -> None:
        self._attempt = (agent_task_id, step_id, agent_id, capability)
        self._calls_in_attempt = 0

    def add(
        self,
        client: ModelClient,
        entry_id: str | None,
        latency_ms: int,
        result: CallResult,
        response: ModelResponse | None,
    ) -> None:
        """Record a call made during the current attempt; a call outside any attempt is not kept."""
        if self._attempt is None:
            return
        agent_task_id, step_id, agent_id, capability = self._attempt
        self._calls_in_attempt += 1
        prompt = response.prompt_tokens if isinstance(response, ModelResponse) else None
        completion = response.completion_tokens if isinstance(response, ModelResponse) else None
        try:
            record = CallRecord(
                agent_task_id=agent_task_id,
                step_id=step_id,
                call_no=self._calls_in_attempt,
                entry_id=entry_id,
                provider=client.provider,
                model=client.model,
                role=agent_id,
                task_type=capability,
                latency_ms=latency_ms,
                prompt_tokens=prompt,
                completion_tokens=completion,
                result=result,
            )
        except ValidationError:
            self.dropped += 1
            return
        self._records.append(record)


class RecordingClient:
    """Same interface as the client it wraps. Never changes the reply or the error."""

    def __init__(
        self,
        inner: ModelClient,
        log: CallLog,
        entry_id: str | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._log = log
        self._entry_id = entry_id
        self._clock = clock
        self.provider = inner.provider
        self.model = inner.model

    def complete(self, request: ModelRequest) -> ModelResponse:
        start = self._clock()
        try:
            response = self._inner.complete(request)
        except ProviderError as exc:
            self._log.add(
                self._inner,
                self._entry_id,
                self._elapsed_ms(start),
                CallResult(exc.kind.value),
                None,
            )
            raise
        except Exception:
            self._log.add(
                self._inner, self._entry_id, self._elapsed_ms(start), CallResult.CLIENT_ERROR, None
            )
            raise
        result = CallResult.OK if isinstance(response, ModelResponse) else CallResult.CLIENT_ERROR
        self._log.add(self._inner, self._entry_id, self._elapsed_ms(start), result, response)
        return response

    def _elapsed_ms(self, start: float) -> int:
        return max(0, round((self._clock() - start) * 1000))
