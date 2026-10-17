"""Agent contracts: tasks, context, results and capability descriptions (ADR-0009)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from cognitive_mesh.graph import DependencyGraph
from cognitive_mesh.ingestion import IngestionResult


class AgentRegistryError(Exception):
    """Duplicate or unknown agent id. The only exception the agents package raises while running."""


class FailureKind(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MISSING_CONTEXT = "missing_context"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_OUTPUT = "malformed_output"
    INTERNAL_ERROR = "internal_error"


class Basis(StrEnum):
    DETERMINISTIC = "deterministic"  # computed from the graph/ingestion data, no model
    MODEL_UNVERIFIED = "model_unverified"  # a model wrote it; not proof of truth


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvidenceRef(_Frozen):
    """Points back at ingested input: relative path + sha256 of the original bytes."""

    path: str
    sha256: str
    line: int | None
    rule: str


class ModelInfo(_Frozen):
    provider: str
    model: str


class CapabilitySpec(_Frozen):
    name: str
    description: str
    uses_model: bool
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class AgentSpec(_Frozen):
    id: str
    role: str
    description: str
    capabilities: tuple[CapabilitySpec, ...]


class AgentTask(_Frozen):
    """A request to one capability. Ids are restricted so they are safe to echo in results."""

    task_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    inputs: dict[str, JsonValue]  # untrusted until the capability validates it


@dataclass(frozen=True)
class AgentContext:
    """What an agent may read. Agents never touch the disk or the network themselves."""

    ingestion: IngestionResult | None = None
    graph: DependencyGraph | None = None


class AgentSuccess(_Frozen):
    status: Literal["success"] = "success"
    agent_id: str
    task_id: str
    capability: str
    output: dict[str, JsonValue]
    basis: Basis
    evidence: tuple[EvidenceRef, ...] = ()
    model: ModelInfo | None = None


class AgentFailure(_Frozen):
    status: Literal["failure"] = "failure"
    agent_id: str
    task_id: str
    capability: str
    kind: FailureKind
    message: str  # never contains input values, file content, model text or exception text
    retryable: bool = False


AgentResult = Annotated[AgentSuccess | AgentFailure, Field(discriminator="status")]
