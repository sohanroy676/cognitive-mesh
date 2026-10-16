"""Configuration loading. All settings come from the environment or an ignored `.env` file."""

from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    log_level: str = "INFO"

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


def load_settings(**overrides: Any) -> Settings:
    """Load settings, raising ConfigError with a clear, secret-free message on failure."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            msg = str(err["msg"]).removeprefix("Value error, ")
            if err["loc"]:
                problems.append(f"{_env_name(err['loc'])}: {msg}")
            else:
                problems.append(msg)
        raise ConfigError(
            "Invalid configuration:\n  - "
            + "\n  - ".join(problems)
            + "\nSet these in the environment or in a local .env file (see .env.example)."
        ) from None
