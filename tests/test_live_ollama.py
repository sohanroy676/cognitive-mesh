"""Opt-in test against a real local Ollama. Skipped unless RUN_LIVE_OLLAMA=1 (never set in CI).

Run: RUN_LIVE_OLLAMA=1 uv run pytest -m live  (needs `ollama pull llama3.2`).
Optional: RUN_LIVE_OLLAMA_URL, RUN_LIVE_OLLAMA_MODEL.
"""

import os

import pytest

from cognitive_mesh.providers import ModelRequest, OllamaClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_OLLAMA") != "1", reason="set RUN_LIVE_OLLAMA=1"),
]


def _client() -> OllamaClient:
    return OllamaClient(
        os.environ.get("RUN_LIVE_OLLAMA_URL", "http://localhost:11434"),
        os.environ.get("RUN_LIVE_OLLAMA_MODEL", "llama3.2"),
    )


def test_a_real_ollama_returns_json_text() -> None:
    response = _client().complete(
        ModelRequest(
            system='Reply with only a JSON object {"ok": true}.',
            prompt="ping",
            timeout_s=120,
            max_output_chars=2000,
        )
    )
    assert response.provider == "ollama"
    assert response.text.strip().startswith("{")
