"""Build a ModelClient from settings (ADR-0012). Makes no network call."""

from cognitive_mesh.config import ConfigError, Settings
from cognitive_mesh.providers.base import ModelClient
from cognitive_mesh.providers.ollama import OllamaClient


def build_model_client(settings: Settings) -> ModelClient:
    if settings.provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model)
    raise ConfigError(
        f"COGMESH_PROVIDER={settings.provider}: no client is implemented for this provider yet "
        "(Groq is planned for v0.6.2). Use COGMESH_PROVIDER=ollama."
    )
