"""Build a ModelClient from settings (ADR-0012, ADR-0013). Makes no network call."""

from cognitive_mesh.config import ConfigError, Settings
from cognitive_mesh.providers.base import ModelClient
from cognitive_mesh.providers.groq import GroqClient
from cognitive_mesh.providers.ollama import OllamaClient


def build_model_client(settings: Settings) -> ModelClient:
    if settings.provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model)
    if settings.provider == "groq":
        if settings.groq_api_key is None:  # Settings already rejects this; keep the guard explicit
            raise ConfigError("GROQ_API_KEY is required when COGMESH_PROVIDER=groq")
        return GroqClient(
            settings.groq_api_key, settings.groq_model, settings.groq_reasoning_effort
        )
    raise ConfigError(f"COGMESH_PROVIDER={settings.provider}: no client is implemented for it")
