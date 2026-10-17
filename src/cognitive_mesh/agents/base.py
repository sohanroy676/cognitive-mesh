"""The common agent interface. `Agent.run` never raises and never reports a failure as success."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from cognitive_mesh.agents.models import (
    AgentContext,
    AgentFailure,
    AgentResult,
    AgentSpec,
    AgentSuccess,
    AgentTask,
    Basis,
    CapabilitySpec,
    EvidenceRef,
    FailureKind,
    ModelInfo,
)
from cognitive_mesh.graph import DependencyGraph
from cognitive_mesh.ingestion import IngestionResult
from cognitive_mesh.providers import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)


class AgentFault(Exception):
    """Raised inside a handler for an expected problem.

    The message must be written by us only: never put input values, file content, model text or
    another exception's text in it.
    """

    def __init__(self, kind: FailureKind, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class Handled:
    """What a handler returns on success."""

    output: BaseModel
    evidence: tuple[EvidenceRef, ...] = ()
    model: ModelInfo | None = None


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[Any, AgentContext], Handled]
    uses_model: bool = False


def require_graph(context: AgentContext) -> DependencyGraph:
    if context.graph is None:
        raise AgentFault(FailureKind.MISSING_CONTEXT, "a dependency graph is required")
    return context.graph


def require_ingestion(context: AgentContext) -> IngestionResult:
    if context.ingestion is None:
        raise AgentFault(FailureKind.MISSING_CONTEXT, "an ingestion result is required")
    return context.ingestion


_PROVIDER_KINDS = {
    ProviderErrorKind.TIMEOUT: (FailureKind.PROVIDER_TIMEOUT, "the model call timed out"),
    ProviderErrorKind.UNAVAILABLE: (
        FailureKind.PROVIDER_UNAVAILABLE,
        "the model provider is unavailable",
    ),
}


def call_model(client: ModelClient | None, request: ModelRequest) -> ModelResponse:
    """The only place provider behavior is handled; every failure becomes an AgentFault."""
    if client is None:
        raise AgentFault(FailureKind.PROVIDER_UNAVAILABLE, "no model client is configured")
    try:
        response = client.complete(request)
    except ProviderError as exc:
        kind, message = _PROVIDER_KINDS.get(
            exc.kind, (FailureKind.PROVIDER_ERROR, f"the model provider failed ({exc.kind.value})")
        )
        raise AgentFault(kind, message, retryable=exc.retryable) from None
    except Exception:  # noqa: BLE001 - any client bug must become an explicit failure
        raise AgentFault(FailureKind.PROVIDER_ERROR, "the model client raised an error") from None
    if not isinstance(response, ModelResponse):
        raise AgentFault(FailureKind.MALFORMED_OUTPUT, "the model client returned a non-response")
    if len(response.text) > request.max_output_chars:
        raise AgentFault(FailureKind.MALFORMED_OUTPUT, "the model reply exceeds the size limit")
    return response


def _describe_validation(exc: ValidationError, model: type[BaseModel]) -> str:
    """Field names and error types only. Input values and unknown keys are untrusted."""
    known = set(model.model_fields)
    parts = []
    for err in exc.errors():
        loc = err["loc"][0] if err["loc"] else None
        name = loc if isinstance(loc, str) and loc in known else "<input>"
        parts.append(f"{name} ({err['type']})")
    return "; ".join(sorted(set(parts)))


class Agent:
    """Base for all agents. Subclasses set id/role/description and pass their capabilities."""

    id: str
    role: str
    description: str

    def __init__(self, capabilities: Sequence[Capability]) -> None:
        if not (self.id and self.role and self.description):
            raise ValueError("an agent needs an id, role and description")
        names = [c.name for c in capabilities]
        if not names or len(set(names)) != len(names):
            raise ValueError("an agent needs at least one capability, with unique names")
        self._capabilities = {c.name: c for c in capabilities}

    def spec(self) -> AgentSpec:
        """Derived from the handler table, so a declared capability always has a handler."""
        return AgentSpec(
            id=self.id,
            role=self.role,
            description=self.description,
            capabilities=tuple(
                CapabilitySpec(
                    name=c.name,
                    description=c.description,
                    uses_model=c.uses_model,
                    input_schema=c.input_model.model_json_schema(),
                    output_schema=c.output_model.model_json_schema(),
                )
                for c in sorted(self._capabilities.values(), key=lambda c: c.name)
            ),
        )

    def supports(self, capability: str) -> bool:
        return capability in self._capabilities

    def run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        try:
            return self._run(task, context)
        except AgentFault as exc:
            return self._failure(task, exc.kind, exc.message, retryable=exc.retryable)
        except Exception as exc:  # noqa: BLE001 - run must never raise
            return self._failure(
                task, FailureKind.INTERNAL_ERROR, f"unexpected {type(exc).__name__}"
            )

    def _run(self, task: AgentTask, context: AgentContext) -> AgentResult:
        cap = self._capabilities.get(task.capability)
        if cap is None:
            supported = ", ".join(sorted(self._capabilities))
            return self._failure(
                task,
                FailureKind.UNSUPPORTED_CAPABILITY,
                f"this agent does not implement that capability; supported: {supported}",
            )
        try:
            validated = cap.input_model.model_validate(task.inputs, strict=True)
        except ValidationError as exc:
            return self._failure(
                task,
                FailureKind.INVALID_INPUT,
                f"invalid inputs: {_describe_validation(exc, cap.input_model)}",
            )
        handled = cap.handler(validated, context)
        if not isinstance(handled, Handled) or not isinstance(handled.output, cap.output_model):
            return self._failure(
                task, FailureKind.INTERNAL_ERROR, "the handler returned an invalid result"
            )
        if cap.uses_model != (handled.model is not None):
            return self._failure(
                task, FailureKind.INTERNAL_ERROR, "model use does not match the declaration"
            )
        return AgentSuccess(
            agent_id=self.id,
            task_id=task.task_id,
            capability=task.capability,
            output=handled.output.model_dump(mode="json"),
            basis=Basis.MODEL_UNVERIFIED if handled.model else Basis.DETERMINISTIC,
            evidence=handled.evidence,
            model=handled.model,
        )

    def _failure(
        self, task: AgentTask, kind: FailureKind, message: str, *, retryable: bool = False
    ) -> AgentFailure:
        return AgentFailure(
            agent_id=self.id,
            task_id=task.task_id,
            capability=task.capability,
            kind=kind,
            message=message,
            retryable=retryable,
        )
