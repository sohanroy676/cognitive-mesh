"""Build a ModelClient from settings (ADR-0012, ADR-0013, ADR-0038). Makes no network call."""

import os
import sys
from collections.abc import Callable, Collection

from dotenv import dotenv_values
from pydantic import SecretStr

from cognitive_mesh.config import ConfigError, Settings
from cognitive_mesh.providers.base import ModelClient
from cognitive_mesh.providers.catalog import CatalogEntry, CatalogError, load_catalog
from cognitive_mesh.providers.groq import GroqClient
from cognitive_mesh.providers.ollama import OllamaClient
from cognitive_mesh.providers.openai_compat import OpenAICompatClient


def _stderr_warning(message: str) -> None:
    print(message, file=sys.stderr)


def catalog_entry_id(settings: Settings) -> str | None:
    """The catalog entry in use, or None when the groq/ollama settings pick the model."""
    return settings.model if settings.provider == "catalog" else None


def read_key(variable: str) -> SecretStr:
    """The key from the process environment, then the ignored `.env`; never echoed in an error."""
    value = os.environ.get(variable) or dotenv_values(".env").get(variable)
    if value is None or not value.strip():
        raise ConfigError(f"{variable} is not set (the catalog entry names it as its key variable)")
    if not all("!" <= ch <= "~" for ch in value):
        raise ConfigError(f"{variable} must be printable ASCII with no spaces or line breaks")
    return SecretStr(value)


def _catalog_client(
    settings: Settings, roles: Collection[str], warn: Callable[[str], None]
) -> ModelClient:
    if settings.model is None or settings.model_catalog is None:  # Settings already rejects this
        raise ConfigError("COGMESH_MODEL and COGMESH_MODEL_CATALOG are required with catalog")
    try:
        catalog = load_catalog(settings.model_catalog)
    except CatalogError as exc:
        raise ConfigError(f"model catalog: {exc}") from None
    entry = catalog.get(settings.model)
    if entry is None:
        raise ConfigError(f"COGMESH_MODEL: no entry named {settings.model} in the catalog")
    missing = sorted(set(roles) - set(entry.roles))
    if missing:
        raise ConfigError(
            f"catalog entry {settings.model} does not list the role(s) this run needs: "
            + ", ".join(missing)
        )
    if entry.remote:
        _require_opt_ins(settings, entry)
    client = _build_entry_client(settings.model, entry)
    if entry.remote:
        warn(
            f"warning: file contents go to {entry.host} (catalog entry {settings.model}, "
            f"tier {entry.tier}, data_use {entry.data_use}, as declared in the catalog)"
        )
    return client


def _require_opt_ins(settings: Settings, entry: CatalogEntry) -> None:
    if not settings.allow_remote_content:
        raise ConfigError(
            f"catalog entry {settings.model} sends file contents to {entry.host}; "
            "set COGMESH_ALLOW_REMOTE_CONTENT=true to allow it"
        )
    if entry.data_use != "no_training" and not settings.allow_training_use:
        raise ConfigError(
            f"catalog entry {settings.model} declares data_use={entry.data_use} for {entry.host}; "
            "set COGMESH_ALLOW_TRAINING_USE=true to allow prompts that may be used for training"
        )


def _build_entry_client(entry_id: str, entry: CatalogEntry) -> ModelClient:
    if entry.wire == "ollama":
        return OllamaClient(entry.base_url, entry.model)
    key = read_key(entry.api_key_env) if entry.api_key_env is not None else None
    return OpenAICompatClient(
        entry_id,
        entry.base_url,
        entry.model,
        key,
        json_mode=entry.json_mode,
        max_tokens_field=entry.max_tokens_field,
        reasoning_effort=entry.reasoning_effort,
    )


def build_model_client(
    settings: Settings,
    roles: Collection[str] = (),
    *,
    warn: Callable[[str], None] = _stderr_warning,
) -> ModelClient:
    """`roles` are the agent roles that will use the client; a catalog entry must list them all."""
    if settings.provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model)
    if settings.provider == "groq":
        if settings.groq_api_key is None:  # Settings already rejects this; keep the guard explicit
            raise ConfigError("GROQ_API_KEY is required when COGMESH_PROVIDER=groq")
        if not settings.allow_remote_content:
            raise ConfigError(
                "COGMESH_PROVIDER=groq sends file contents to Groq's servers; "
                "set COGMESH_ALLOW_REMOTE_CONTENT=true to allow it"
            )
        return GroqClient(
            settings.groq_api_key, settings.groq_model, settings.groq_reasoning_effort
        )
    if settings.provider == "catalog":
        return _catalog_client(settings, roles, warn)
    raise ConfigError(f"COGMESH_PROVIDER={settings.provider}: no client is implemented for it")
