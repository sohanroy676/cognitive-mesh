from pathlib import Path

import pytest

from cognitive_mesh.config import ConfigError, load_settings, load_settings_if_configured

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


def test_model_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    settings = load_settings()
    assert settings.ollama_model == "llama3.2"
    assert settings.model_timeout_s == 30.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("COGMESH_MODEL_TIMEOUT_S", "0"),
        ("COGMESH_MODEL_TIMEOUT_S", "-1"),
        ("COGMESH_MODEL_TIMEOUT_S", "601"),
        ("COGMESH_MODEL_TIMEOUT_S", "nan"),
        ("COGMESH_MODEL_TIMEOUT_S", "soon"),
        ("COGMESH_OLLAMA_MODEL", ""),
        ("COGMESH_OLLAMA_MODEL", "  "),
        ("COGMESH_OLLAMA_MODEL", "a b"),
        ("COGMESH_OLLAMA_MODEL", "-leading"),
        ("COGMESH_OLLAMA_MODEL", "x" * 201),
        ("COGMESH_OLLAMA_BASE_URL", "ftp://localhost"),
        ("COGMESH_OLLAMA_BASE_URL", "localhost:11434"),
        ("COGMESH_OLLAMA_BASE_URL", "http://"),
        ("COGMESH_OLLAMA_BASE_URL", "http://user:pw@localhost"),
        ("COGMESH_OLLAMA_BASE_URL", "http://localhost?x=1"),
        ("COGMESH_OLLAMA_BASE_URL", "http://localhost#f"),
        ("COGMESH_OLLAMA_BASE_URL", "http://localhost:99999"),
    ],
    ids=range(17),
)
def test_invalid_model_settings_name_the_variable_and_never_echo_the_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert name in str(exc.value)
    assert "pw@" not in str(exc.value)


def test_valid_model_settings_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    monkeypatch.setenv("COGMESH_OLLAMA_MODEL", "hf.co/user/model:Q4_K_M")
    monkeypatch.setenv("COGMESH_MODEL_TIMEOUT_S", "120.5")
    monkeypatch.setenv("COGMESH_OLLAMA_BASE_URL", "https://gpu-box.local:8443")
    settings = load_settings()
    assert settings.ollama_model == "hf.co/user/model:Q4_K_M"
    assert settings.model_timeout_s == 120.5


def test_if_configured_returns_none_only_when_the_provider_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_settings_if_configured() is None
    monkeypatch.setenv("COGMESH_PROVIDER", "ollama")
    settings = load_settings_if_configured()
    assert settings is not None
    assert settings.provider == "ollama"


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"COGMESH_PROVIDER": "bogus"}, "COGMESH_PROVIDER"),
        ({"COGMESH_PROVIDER": "groq"}, "GROQ_API_KEY"),
        ({"COGMESH_MODEL_TIMEOUT_S": "0"}, "COGMESH_MODEL_TIMEOUT_S"),  # provider also missing
    ],
    ids=range(3),
)
def test_if_configured_still_raises_for_every_other_problem(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], needle: str
) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ConfigError, match=needle):
        load_settings_if_configured()
