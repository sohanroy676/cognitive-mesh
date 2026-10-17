"""Opt-in test against the real Groq API. Skipped unless RUN_LIVE_GROQ=1 (never set in CI).

Run: RUN_LIVE_GROQ=1 RUN_LIVE_GROQ_KEY=<your key> uv run pytest -m live
Optional: RUN_LIVE_GROQ_MODEL. The key comes from RUN_LIVE_GROQ_KEY because the test fixtures
clear GROQ_API_KEY. It is never printed.
"""

import os

import pytest
from pydantic import SecretStr

from cognitive_mesh.providers import GroqClient, ModelRequest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_GROQ") != "1", reason="set RUN_LIVE_GROQ=1"),
]


def _client() -> GroqClient:
    key = os.environ.get("RUN_LIVE_GROQ_KEY", "")
    if not key:
        pytest.skip("set RUN_LIVE_GROQ_KEY")
    model = os.environ.get("RUN_LIVE_GROQ_MODEL", "qwen/qwen3.8-27b")
    return GroqClient(SecretStr(key), model, "none")


def test_a_real_groq_returns_json_text() -> None:
    response = _client().complete(
        ModelRequest(
            system="Reply with a JSON object only.",
            prompt='Return {"ok": true}.',
            timeout_s=60,
            max_output_chars=2000,
        )
    )
    assert response.provider == "groq"
    assert "ok" in response.text
