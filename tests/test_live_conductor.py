"""Opt-in: `task --conduct` against a real OpenAI-compatible endpoint (ADR-0040). Skipped unless
RUN_LIVE_CONDUCTOR=1 (never set in CI).

Run: RUN_LIVE_CONDUCTOR=1 RUN_LIVE_COMPAT_BASE_URL=<url ending before /chat/completions>
     RUN_LIVE_COMPAT_MODEL=<model> RUN_LIVE_COMPAT_KEY=<key> uv run pytest -m live
One catalog entry plays the conductor and the explainer. A real model may draft a plan the
validators refuse, so the test accepts exit 22 as long as nothing was stored; it checks that the
pipeline holds together, not that the model is good.
"""

import os
from pathlib import Path

import pytest

from cognitive_mesh.cli import EXIT_CONDUCTOR_ERROR, EXIT_OK, EXIT_TASK_NOT_SUCCEEDED, main
from cognitive_mesh.traces import TraceStore

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_CONDUCTOR") != "1", reason="set RUN_LIVE_CONDUCTOR=1"
    ),
]

APP = Path(__file__).parent / "fixtures" / "sample_app"


def test_a_real_model_conducts_a_task_or_is_refused_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url = os.environ.get("RUN_LIVE_COMPAT_BASE_URL", "")
    model = os.environ.get("RUN_LIVE_COMPAT_MODEL", "")
    if not base_url or not model:
        pytest.skip("set RUN_LIVE_COMPAT_BASE_URL and RUN_LIVE_COMPAT_MODEL")
    key = os.environ.get("RUN_LIVE_COMPAT_KEY", "")
    remote = base_url.startswith("https://")
    key_line = 'api_key_env = "LIVE_COMPAT_API_KEY"\n' if key or remote else ""
    catalog = tmp_path / "catalog.toml"
    catalog.write_text(
        f'[models.live]\nwire = "openai_compatible"\nbase_url = "{base_url}"\nmodel = "{model}"\n'
        f'{key_line}context_tokens = 8192\ntier = "free"\ndata_use = "unknown"\n'
        'roles = ["conductor", "code_explainer"]\n',
        encoding="utf-8",
    )
    if key:
        monkeypatch.setenv("LIVE_COMPAT_API_KEY", key)
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    monkeypatch.setenv("COGMESH_ALLOW_TRAINING_USE", "true")
    db = tmp_path / "t.sqlite3"
    code = main(
        [
            "task",
            "explain the project file",
            "--conduct",
            "--conductor",
            "live",
            "--catalog",
            str(catalog),
            "--root",
            str(APP),
            "--db",
            str(db),
            "--index",
            str(tmp_path / "idx"),
            "--graph",
            str(tmp_path / "g.sqlite3"),
        ]
    )
    assert code in (EXIT_OK, EXIT_TASK_NOT_SUCCEEDED, EXIT_CONDUCTOR_ERROR)
    with TraceStore.open_existing(db) as store:
        runs = store.list().runs
        if code == EXIT_CONDUCTOR_ERROR:
            assert runs == () and store.read_decisions().decisions == ()
        else:
            assert len(runs) == 1
            assert len(store.read_decisions().decisions) >= 1
            assert any(r.call.role == "conductor" for r in store.call_rows().rows)
