"""Opt-in test against a real OpenAI-compatible endpoint (ADR-0038). Skipped unless
RUN_LIVE_OPENAI_COMPAT=1 (never set in CI).

Run: RUN_LIVE_OPENAI_COMPAT=1 RUN_LIVE_COMPAT_BASE_URL=<url ending before /chat/completions>
     RUN_LIVE_COMPAT_MODEL=<model> RUN_LIVE_COMPAT_KEY=<key> uv run pytest -m live
The key is optional for a local server. It is read from RUN_LIVE_COMPAT_KEY because the test
fixtures clear other variables, and it is never printed.
"""

import os

import pytest
from pydantic import SecretStr

from cognitive_mesh.providers import ModelRequest, OpenAICompatClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_OPENAI_COMPAT") != "1", reason="set RUN_LIVE_OPENAI_COMPAT=1"
    ),
]


def _client() -> OpenAICompatClient:
    base_url = os.environ.get("RUN_LIVE_COMPAT_BASE_URL", "")
    model = os.environ.get("RUN_LIVE_COMPAT_MODEL", "")
    if not base_url or not model:
        pytest.skip("set RUN_LIVE_COMPAT_BASE_URL and RUN_LIVE_COMPAT_MODEL")
    key = os.environ.get("RUN_LIVE_COMPAT_KEY", "")
    return OpenAICompatClient("live", base_url, model, SecretStr(key) if key else None)


def test_a_real_endpoint_returns_json_text_and_reports_usage_or_unknown() -> None:
    response = _client().complete(
        ModelRequest(
            system="Reply with a JSON object only.",
            prompt='Return {"ok": true}.',
            timeout_s=60,
            max_output_chars=2000,
        )
    )
    assert response.provider == "live"
    assert "ok" in response.text
    for count in (response.prompt_tokens, response.completion_tokens):
        assert count is None or count >= 0
