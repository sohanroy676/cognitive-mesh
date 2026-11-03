from pathlib import Path

import pytest

from cognitive_mesh.agents import default_registry
from cognitive_mesh.providers.catalog import (
    AGENT_ROLES,
    MAX_CATALOG_BYTES,
    CatalogError,
    load_catalog,
    parse_catalog,
)
from providers_support import fake_key

GOOD = """
[models.gemini-flash]
wire = "openai_compatible"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
model = "gemini-2.5-flash"
api_key_env = "GEMINI_API_KEY"
context_tokens = 1000000
tier = "free"
data_use = "may_train"
roles = ["code_explainer", "planner"]

[models.local-llama]
wire = "ollama"
base_url = "http://localhost:11434"
model = "llama3.2"
context_tokens = 8192
tier = "free"
data_use = "no_training"
roles = ["code_explainer"]
"""


def _entry(**changes: object) -> str:
    fields: dict[str, object] = {
        "wire": "openai_compatible",
        "base_url": "https://llm.example.test/v1",
        "model": "m1",
        "api_key_env": "EXAMPLE_API_KEY",
        "context_tokens": 4096,
        "tier": "free",
        "data_use": "unknown",
        "roles": ["code_explainer"],
    }
    fields.update(changes)
    lines = ["[models.x]"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key} = {value!r}".replace("'", '"').replace("True", "true"))
    return "\n".join(lines)


def test_a_good_catalog_loads_and_reports_remote_and_loopback() -> None:
    catalog = parse_catalog(GOOD)
    gem, local = catalog.models["gemini-flash"], catalog.models["local-llama"]
    assert gem.remote and gem.host == "generativelanguage.googleapis.com"
    assert not local.remote and local.api_key_env is None
    assert gem.data_use == "may_train" and gem.tier == "free"
    assert gem.max_tokens_field == "max_tokens" and gem.json_mode is True


def test_the_role_list_matches_the_registry() -> None:
    agent_ids = {spec.id for spec in default_registry().specs()}
    assert agent_ids | {"planner"} == AGENT_ROLES


@pytest.mark.parametrize(
    "toml",
    [
        _entry(base_url="http://llm.example.test/v1"),  # remote over http
        _entry(base_url="https://user:pw@llm.example.test/v1"),
        _entry(base_url="https://llm.example.test/v1?x=1"),
        _entry(base_url="ftp://llm.example.test/v1"),
        _entry(base_url="https://llm.example.test:notaport/v1"),
        _entry(api_key_env=None),  # remote without a key variable
        _entry(api_key_env="lowercase_key"),
        _entry(api_key_env="GEMINI_TOKEN"),
        _entry(wire="ollama", base_url="http://localhost:11434"),  # ollama with a key variable
        _entry(roles=["wizard"]),
        _entry(roles=[]),
        _entry(roles=["planner", "planner"]),
        _entry(tier="gold"),
        _entry(data_use="trust me"),
        _entry(context_tokens=0),
        _entry(model="bad model name"),
        _entry(extra_field="x"),
        _entry(reasoning_effort="extreme"),
    ],
)
def test_invalid_entries_are_refused(toml: str) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(toml)


def test_http_is_fine_for_loopback_and_a_key_is_optional_there() -> None:
    catalog = parse_catalog(
        _entry(base_url="http://127.0.0.1:8080/v1", api_key_env=None, wire="openai_compatible")
    )
    assert not catalog.models["x"].remote


def test_a_secret_shaped_value_is_refused_without_echoing_it() -> None:
    key = fake_key("gsk_")
    with pytest.raises(CatalogError) as info:
        parse_catalog(_entry(model=key))
    assert key not in str(info.value) and "looks like a secret" in str(info.value)
    with pytest.raises(CatalogError) as info2:
        parse_catalog(_entry(api_key_env=None, base_url=f"https://u:{key}@h.example.test/"))
    assert key not in str(info2.value)


def test_errors_never_echo_the_bad_value() -> None:
    with pytest.raises(CatalogError) as info:
        parse_catalog(_entry(tier="CANARY-TIER-123"))
    assert "CANARY-TIER-123" not in str(info.value) and "entry x, tier" in str(info.value)
    with pytest.raises(CatalogError) as info2:
        parse_catalog(_entry(**{"CANARY-FIELD-9": "v"}))
    assert "CANARY-FIELD-9" not in str(info2.value)


@pytest.mark.parametrize(
    "text",
    ["", "not = [valid", "[other]\na = 1", "[models]\n", "models = 3", GOOD + "\n[extra]\na=1"],
)
def test_structure_problems_are_refused(text: str) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(text)


def test_entry_ids_are_restricted() -> None:
    with pytest.raises(CatalogError):
        parse_catalog(GOOD.replace("local-llama", "Bad Id"))


def test_file_problems(tmp_path: Path) -> None:
    with pytest.raises(CatalogError):
        load_catalog(tmp_path / "missing.toml")
    big = tmp_path / "big.toml"
    big.write_bytes(b"#" * (MAX_CATALOG_BYTES + 1))
    with pytest.raises(CatalogError):
        load_catalog(big)
    binary = tmp_path / "bin.toml"
    binary.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(CatalogError):
        load_catalog(binary)
    ok = tmp_path / "ok.toml"
    ok.write_text(GOOD, encoding="utf-8")
    assert set(load_catalog(ok).models) == {"gemini-flash", "local-llama"}


def test_a_key_variable_shared_across_hosts_is_reported() -> None:
    two = GOOD + GOOD.split("[models.local-llama]")[0].replace("gemini-flash", "other").replace(
        "generativelanguage.googleapis.com/v1beta/openai", "openrouter.example.test/api"
    )
    catalog = parse_catalog(two)
    shared = catalog.key_env_hosts()
    assert shared == {
        "GEMINI_API_KEY": ("generativelanguage.googleapis.com", "openrouter.example.test")
    }
