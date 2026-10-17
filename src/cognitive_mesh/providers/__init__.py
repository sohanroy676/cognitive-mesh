"""Model provider boundary: the protocol and errors (v0.4.0), plus the Ollama client (v0.6.1)."""

from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.factory import build_model_client
from cognitive_mesh.providers.ollama import OllamaClient

__all__ = [
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "OllamaClient",
    "ProviderError",
    "ProviderErrorKind",
    "build_model_client",
]
