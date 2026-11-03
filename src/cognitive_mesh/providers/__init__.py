"""Model provider boundary: protocol, errors and the Ollama, Groq and OpenAI-compatible clients."""

from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.catalog import Catalog, CatalogEntry, CatalogError, load_catalog
from cognitive_mesh.providers.factory import build_model_client, catalog_entry_id
from cognitive_mesh.providers.groq import GroqClient
from cognitive_mesh.providers.ollama import OllamaClient
from cognitive_mesh.providers.openai_compat import OpenAICompatClient

__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "GroqClient",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "OllamaClient",
    "OpenAICompatClient",
    "ProviderError",
    "ProviderErrorKind",
    "build_model_client",
    "catalog_entry_id",
    "load_catalog",
]
