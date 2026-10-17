"""Model provider boundary: protocol, errors and the Ollama and Groq clients."""

from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.factory import build_model_client
from cognitive_mesh.providers.groq import GroqClient
from cognitive_mesh.providers.ollama import OllamaClient

__all__ = [
    "GroqClient",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "OllamaClient",
    "ProviderError",
    "ProviderErrorKind",
    "build_model_client",
]
