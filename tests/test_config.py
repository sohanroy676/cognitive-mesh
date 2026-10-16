from pathlib import Path

import pytest

from cognitive_mesh.config import ConfigError, load_settings

SECRET = "gsk_test_secret_value_123"  # noqa: S105 - fake value for tests


def test_ollama_config_loads_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    settings = load_settings()
    assert settings.provider == "ollama"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.groq_api_key is None


def test_missing_provider_names_the_variable() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "COGMESH_PROVIDER" in str(exc.value)
    assert ".env.example" in str(exc.value)


def test_invalid_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "openai")
    with pytest.raises(ConfigError, match="COGMESH_PROVIDER"):
        load_settings()


@pytest.mark.parametrize("key", [None, "", "   "])
def test_groq_requires_nonblank_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "groq")
    if key is not None:
        monkeypatch.setenv("GROQ_API_KEY", key)
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        load_settings()


def test_secret_never_appears_in_repr_str_or_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    settings = load_settings()
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == SECRET
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)

    monkeypatch.setenv("COGMESH_PROVIDER", "bogus")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert SECRET not in str(exc.value)


def test_dotenv_file_in_cwd_is_read(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        f"COGMESH_PROVIDER=groq\nGROQ_API_KEY={SECRET}\nUNRELATED=1\n", encoding="utf-8"
    )
    settings = load_settings()
    assert settings.provider == "groq"
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == SECRET


def test_environment_overrides_dotenv(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated_env / ".env").write_text("COGMESH_PROVIDER=ollama\n", encoding="utf-8")
    monkeypatch.setenv("COGMESH_LOG_LEVEL", "DEBUG")
    assert load_settings().log_level == "DEBUG"
