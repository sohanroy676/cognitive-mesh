"""Configuration loading. All settings come from the environment or an ignored `.env` file."""

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COGMESH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    provider: Literal["groq", "ollama"]
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    model_timeout_s: float = Field(default=30.0, gt=0, le=600)
    log_level: str = "INFO"

    @field_validator("ollama_base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        try:
            parts = urlsplit(value)
            parts.port  # noqa: B018 - raises ValueError on an invalid port
        except ValueError:
            raise ValueError("must be a valid http(s) URL") from None
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("must be an http(s) URL with a host and no credentials or query")
        return value

    @field_validator("ollama_model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        if len(value) > 200 or not _MODEL_NAME.fullmatch(value):
            raise ValueError("must be a model name of letters, digits and . _ : / - (max 200)")
        return value

    @model_validator(mode="after")
    def _require_key_for_groq(self) -> "Settings":
        if self.provider == "groq" and (
            self.groq_api_key is None or not self.groq_api_key.get_secret_value().strip()
        ):
            raise ValueError("GROQ_API_KEY is required when COGMESH_PROVIDER=groq")
        return self


def _env_name(loc: tuple[int | str, ...]) -> str:
    if not loc:
        return "settings"
    name = str(loc[0])
    return name if name == "GROQ_API_KEY" else f"COGMESH_{name.upper()}"


def _to_config_error(exc: ValidationError) -> ConfigError:
    problems = []
    for err in exc.errors():
        msg = str(err["msg"]).removeprefix("Value error, ")
        if err["loc"]:
            problems.append(f"{_env_name(err['loc'])}: {msg}")
        else:
            problems.append(msg)
    return ConfigError(
        "Invalid configuration:\n  - "
        + "\n  - ".join(problems)
        + "\nSet these in the environment or in a local .env file (see .env.example)."
    )


def load_settings(**overrides: Any) -> Settings:
    """Load settings, raising ConfigError with a clear, secret-free message on failure."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        raise _to_config_error(exc) from None


def load_settings_if_configured(**overrides: Any) -> Settings | None:
    """Like load_settings, but None when the only problem is that COGMESH_PROVIDER is not set."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        errors = exc.errors()
        if (
            len(errors) == 1
            and errors[0]["loc"] == ("provider",)
            and errors[0]["type"] == "missing"
        ):
            return None
        raise _to_config_error(exc) from None
