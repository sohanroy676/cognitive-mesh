from pathlib import Path

import pytest

from cognitive_mesh.config import ConfigError, load_settings
from cognitive_mesh.providers import (
    OllamaClient,
    OpenAICompatClient,
    build_model_client,
    catalog_entry_id,
)
from providers_support import fake_key

CATALOG = """
[models.gem]
wire = "openai_compatible"
base_url = "https://llm.example.test/v1"
model = "gemini-x"
api_key_env = "EXAMPLE_API_KEY"
context_tokens = 100000
tier = "free"
data_use = "may_train"
roles = ["code_explainer"]

[models.safe]
wire = "openai_compatible"
base_url = "https://safe.example.test/v1"
model = "safe-1"
api_key_env = "SAFE_API_KEY"
context_tokens = 100000
tier = "paid"
data_use = "no_training"
roles = ["code_explainer", "planner"]

[models.local]
wire = "ollama"
base_url = "http://localhost:11434"
model = "llama3.2"
context_tokens = 8192
tier = "free"
data_use = "no_training"
roles = ["code_explainer"]
"""


@pytest.fixture
def catalog_file(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.toml"
    path.write_text(CATALOG, encoding="utf-8")
    return path


def _settings(catalog_file: Path, model: str, **extra: object):  # type: ignore[no-untyped-def]
    return load_settings(provider="catalog", model=model, model_catalog=catalog_file, **extra)


def _build(settings, roles=("code_explainer",)):  # type: ignore[no-untyped-def]
    warnings: list[str] = []
    client = build_model_client(settings, roles, warn=warnings.append)
    return client, warnings


def test_a_local_entry_needs_no_opt_in_and_no_warning(catalog_file: Path) -> None:
    client, warnings = _build(_settings(catalog_file, "local"))
    assert isinstance(client, OllamaClient) and warnings == []


def test_a_remote_entry_needs_both_opt_ins_and_the_key(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXAMPLE_API_KEY", fake_key())
    with pytest.raises(ConfigError, match="COGMESH_ALLOW_REMOTE_CONTENT"):
        _build(_settings(catalog_file, "gem"))
    with pytest.raises(ConfigError, match="COGMESH_ALLOW_TRAINING_USE"):
        _build(_settings(catalog_file, "gem", allow_remote_content=True))
    client, warnings = _build(
        _settings(catalog_file, "gem", allow_remote_content=True, allow_training_use=True)
    )
    assert isinstance(client, OpenAICompatClient) and client.provider == "gem"
    assert len(warnings) == 1
    assert "llm.example.test" in warnings[0] and "data_use may_train" in warnings[0]
    assert fake_key() not in warnings[0]


def test_a_no_training_entry_needs_only_the_remote_opt_in(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFE_API_KEY", fake_key())
    _, warnings = _build(_settings(catalog_file, "safe", allow_remote_content=True))
    assert len(warnings) == 1 and "safe.example.test" in warnings[0]


def test_a_missing_or_bad_key_is_named_but_never_echoed(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(catalog_file, "safe", allow_remote_content=True)
    monkeypatch.delenv("SAFE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="SAFE_API_KEY is not set"):
        _build(settings)
    bad = "has space " + fake_key()
    monkeypatch.setenv("SAFE_API_KEY", bad)
    with pytest.raises(ConfigError) as info:
        _build(settings)
    assert "SAFE_API_KEY" in str(info.value) and fake_key() not in str(info.value)


def test_the_key_can_come_from_the_ignored_dotenv(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SAFE_API_KEY", raising=False)
    Path(".env").write_text(f"SAFE_API_KEY={fake_key()}\n", encoding="utf-8")
    client, _ = _build(_settings(catalog_file, "safe", allow_remote_content=True))
    assert isinstance(client, OpenAICompatClient)


def test_an_unlisted_role_unknown_entry_or_bad_catalog_is_a_config_error(
    catalog_file: Path, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="planner"):
        _build(_settings(catalog_file, "local"), roles=("code_explainer", "planner"))
    with pytest.raises(ConfigError, match="no entry named nope"):
        _build(_settings(catalog_file, "nope"))
    broken = tmp_path / "broken.toml"
    broken.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="model catalog"):
        _build(load_settings(provider="catalog", model="x", model_catalog=broken))
    with pytest.raises(ConfigError, match="model catalog"):
        _build(load_settings(provider="catalog", model="x", model_catalog=tmp_path / "gone.toml"))


def test_catalog_settings_need_both_variables() -> None:
    with pytest.raises(ConfigError, match="COGMESH_MODEL "):
        load_settings(provider="catalog", model_catalog=Path("c.toml"))
    with pytest.raises(ConfigError, match="COGMESH_MODEL_CATALOG"):
        load_settings(provider="catalog", model="x")
    with pytest.raises(ConfigError, match="COGMESH_MODEL"):
        load_settings(provider="catalog", model="Bad Id", model_catalog=Path("c.toml"))


def test_groq_and_ollama_settings_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build(load_settings(provider="ollama"))
    assert isinstance(client, OllamaClient)
    assert catalog_entry_id(load_settings(provider="ollama")) is None


def test_the_entry_id_is_reported_for_catalog_settings(catalog_file: Path) -> None:
    assert catalog_entry_id(_settings(catalog_file, "local")) == "local"
