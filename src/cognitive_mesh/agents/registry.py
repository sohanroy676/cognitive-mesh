"""Discoverable roles and capabilities. The orchestrator selects by capability only."""

from cognitive_mesh.agents.base import Agent
from cognitive_mesh.agents.code_explainer import CodeExplainer
from cognitive_mesh.agents.dependency_analyst import DependencyAnalyst
from cognitive_mesh.agents.models import AgentRegistryError, AgentSpec
from cognitive_mesh.providers import ModelClient


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.id in self._agents:
            raise AgentRegistryError(f"duplicate agent id: {agent.id!r}")
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise AgentRegistryError("unknown agent id") from None

    def specs(self) -> list[AgentSpec]:
        return [self._agents[i].spec() for i in sorted(self._agents)]

    def find(self, capability: str) -> list[Agent]:
        return [a for _, a in sorted(self._agents.items()) if a.supports(capability)]


def default_registry(model_client: ModelClient | None = None) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(DependencyAnalyst())
    registry.register(CodeExplainer(model_client))
    return registry
