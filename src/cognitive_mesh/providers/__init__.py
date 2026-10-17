"""Model provider boundary (v0.4.0): protocol and errors only, no provider clients yet."""

from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)

__all__ = [
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ProviderErrorKind",
]
