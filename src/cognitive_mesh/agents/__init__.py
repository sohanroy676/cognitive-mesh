"""Agent interface, contracts, roles and registry (v0.4.0, ADR-0009)."""

from cognitive_mesh.agents.base import Agent, AgentFault, Capability, Handled, call_model
from cognitive_mesh.agents.code_explainer import CodeExplainer
from cognitive_mesh.agents.context import RecalledContext
from cognitive_mesh.agents.dependency_analyst import DependencyAnalyst
from cognitive_mesh.agents.models import (
    AgentContext,
    AgentFailure,
    AgentRegistryError,
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
from cognitive_mesh.agents.registry import AgentRegistry, default_registry

__all__ = [
    "Agent",
    "AgentContext",
    "AgentFailure",
    "AgentFault",
    "AgentRegistry",
    "AgentRegistryError",
    "AgentResult",
    "AgentSpec",
    "AgentSuccess",
    "AgentTask",
    "Basis",
    "Capability",
    "CapabilitySpec",
    "CodeExplainer",
    "DependencyAnalyst",
    "EvidenceRef",
    "FailureKind",
    "Handled",
    "ModelInfo",
    "RecalledContext",
    "call_model",
    "default_registry",
]
