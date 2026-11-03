"""The model catalog (ADR-0038): which models exist, where they live and what they may be used for.

A TOML file read only from a path the person names (never from an analysed project). It holds
no keys: an entry names the environment variable that does. Error messages name the entry and field
and never a value.
"""

import re
import tomllib
from collections.abc import Iterator
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from cognitive_mesh.ingestion.secrets import redact_secrets

# Roles an entry may list: the ids of agents that use a model, plus plan drafting. A test pins the
# first two to the default registry so this cannot drift.
AGENT_ROLES = frozenset({"code_explainer", "dependency_analyst", "planner"})
MAX_CATALOG_BYTES = 256 * 1024
MAX_ENTRIES = 200

_ENTRY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
_KEY_ENV = re.compile(r"[A-Z][A-Z0-9_]*_API_KEY")
_LOOPBACK_NAMES = frozenset({"localhost"})
_HOST = re.compile(r"[A-Za-z0-9._:-]{1,253}")


class CatalogError(Exception):
    """The catalog is missing, unreadable or invalid. Names entries and fields, never values."""


def is_loopback_host(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class CatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    wire: Literal["openai_compatible", "ollama"]
    base_url: str = Field(max_length=300)
    model: str = Field(max_length=200)
    api_key_env: str | None = None
    context_tokens: int = Field(gt=0, le=100_000_000)
    tier: Literal["free", "paid"]
    data_use: Literal["may_train", "no_training", "unknown"]
    roles: tuple[str, ...] = Field(min_length=1, max_length=len(AGENT_ROLES))
    json_mode: bool = True
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    reasoning_effort: Literal["", "none", "default", "low", "medium", "high"] = ""

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        if not _MODEL_NAME.fullmatch(value):
            raise ValueError("must be a model name of letters, digits and . _ : / -")
        return value

    @field_validator("api_key_env")
    @classmethod
    def _check_key_env(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 80 or not _KEY_ENV.fullmatch(value)):
            raise ValueError("must be an environment variable name ending in _API_KEY")
        return value

    @field_validator("roles")
    @classmethod
    def _check_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - AGENT_ROLES)
        if unknown or len(set(value)) != len(value):
            raise ValueError(f"must be distinct names from: {', '.join(sorted(AGENT_ROLES))}")
        return value

    @model_validator(mode="after")
    def _check_url_and_key(self) -> "CatalogEntry":
        try:
            parts = urlsplit(self.base_url)
            parts.port  # noqa: B018 - raises ValueError on an invalid port
        except ValueError:
            raise ValueError("base_url: must be a valid http(s) URL") from None
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "base_url: must be an http(s) URL with a host and no credentials or query"
            )
        if not _HOST.fullmatch(parts.hostname):
            raise ValueError("base_url: the host has characters a host name cannot have")
        if not is_loopback_host(parts.hostname):
            if parts.scheme != "https":
                raise ValueError("base_url: a remote host must use https")
            if self.api_key_env is None:
                raise ValueError("api_key_env: a remote host needs the name of its key variable")
        if self.wire == "ollama" and self.api_key_env is not None:
            raise ValueError("api_key_env: an ollama entry sends no key")
        return self

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def remote(self) -> bool:
        return not is_loopback_host(self.host)


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    models: dict[str, CatalogEntry]

    def get(self, entry_id: str) -> CatalogEntry | None:
        return self.models.get(entry_id)

    def key_env_hosts(self) -> dict[str, tuple[str, ...]]:
        """Key variable -> hosts that use it, only where more than one host does."""
        hosts: dict[str, set[str]] = {}
        for entry in self.models.values():
            if entry.api_key_env is not None:
                hosts.setdefault(entry.api_key_env, set()).add(entry.host)
        return {k: tuple(sorted(v)) for k, v in sorted(hosts.items()) if len(v) > 1}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def parse_catalog(text: str) -> Catalog:
    """Parse and validate catalog text (the loader below adds the file checks)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise CatalogError("the catalog is not valid TOML") from None
    models = data.get("models")
    if set(data) - {"models"} or not isinstance(models, dict) or not models:
        raise CatalogError("the catalog needs one or more [models.<id>] tables and nothing else")
    if len(models) > MAX_ENTRIES:
        raise CatalogError(f"the catalog has more than {MAX_ENTRIES} entries")
    for entry_id, entry in models.items():
        if not _ENTRY_ID.fullmatch(entry_id):
            raise CatalogError("an entry id must be lowercase letters, digits and . _ - (max 64)")
        if not isinstance(entry, dict):
            raise CatalogError(f"entry {entry_id}: must be a table")
        # A pasted key must not be accepted, stored or echoed anywhere: scan every value.
        if any(redact_secrets(s)[1] for s in _strings(entry)):
            raise CatalogError(f"entry {entry_id}: a value looks like a secret; keys never go here")
    try:
        return Catalog(models=models)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = err["loc"]
        entry_id = str(loc[1]) if len(loc) > 1 else "?"
        field = str(loc[2]) if len(loc) > 2 else ""
        if not re.fullmatch(r"[a-z_]{1,40}", field):
            field = "<unknown field>"
        msg = str(err["msg"]).removeprefix("Value error, ")
        where = f"entry {entry_id}" + (f", {field}" if field else "")
        raise CatalogError(f"{where}: {msg} ({err['type']})") from None


def load_catalog(path: Path) -> Catalog:
    try:
        size = path.stat().st_size
        if size > MAX_CATALOG_BYTES:
            raise CatalogError(f"the catalog file is larger than {MAX_CATALOG_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise CatalogError("cannot read the catalog file") from None
    except UnicodeDecodeError:
        raise CatalogError("the catalog file is not UTF-8 text") from None
    return parse_catalog(text)
