"""`models list|stats` and `--model` on run/plan/task, end to end through main() (ADR-0038)."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cognitive_mesh import cli, models_cli
from cognitive_mesh.cli import (
    EXIT_CATALOG_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_TASK_NOT_SUCCEEDED,
    EXIT_TRACE_STORE_ERROR,
    main,
)
from providers_support import fake_key

FIXTURES = Path(__file__).parent / "fixtures"
SUMMARY = '{"summary": "A tiny sample project.", "symbols": []}'
OPENAI_OK = json.dumps(
    {
        "choices": [
            {"message": {"role": "assistant", "content": SUMMARY}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 15},
    }
).encode()
KEY_CANARY = fake_key("zz_")


@contextmanager
def _server(
    status: int = 200, body: bytes = OPENAI_OK
) -> Iterator[tuple[str, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _catalog(tmp_path: Path, url: str) -> Path:
    path = tmp_path / "catalog.toml"
    path.write_text(
        f"""
[models.local-compat]
wire = "openai_compatible"
base_url = "{url}/v1"
model = "tiny-1"
context_tokens = 4096
tier = "free"
data_use = "no_training"
roles = ["code_explainer", "planner"]

[models.cloud]
wire = "openai_compatible"
base_url = "https://cloud.example.test/v1"
model = "big-1"
api_key_env = "CLOUD_API_KEY"
context_tokens = 100000
tier = "free"
data_use = "may_train"
roles = ["code_explainer"]
""",
        encoding="utf-8",
    )
    return path


def _run(catalog: Path, model: str, *extra: str) -> int:
    return main(
        [
            "run",
            str(FIXTURES / "plans" / "needs_model.json"),
            "--root",
            str(FIXTURES / "sample_app"),
            "--model",
            model,
            "--catalog",
            str(catalog),
            *extra,
        ]
    )


def test_exit_codes_match_the_cli_module() -> None:
    assert models_cli.EXIT_TRACE_STORE_ERROR == cli.EXIT_TRACE_STORE_ERROR == EXIT_TRACE_STORE_ERROR
    assert EXIT_CATALOG_ERROR == 20


def test_list_shows_entries_without_any_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUD_API_KEY", KEY_CANARY)
    catalog = _catalog(tmp_path, "http://127.0.0.1:9")
    assert main(["models", "list", "--catalog", str(catalog)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "cloud: openai_compatible" in out and "data_use may_train" in out
    assert "CLOUD_API_KEY (set)" in out and KEY_CANARY not in out
    assert main(["models", "list", "--catalog", str(catalog), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert [e["id"] for e in payload["entries"]] == ["cloud", "local-compat"]
    assert KEY_CANARY not in json.dumps(payload)
    monkeypatch.delenv("CLOUD_API_KEY")
    main(["models", "list", "--catalog", str(catalog)])
    assert "not set" in capsys.readouterr().out


def test_list_uses_the_environment_variable_and_reports_a_missing_or_bad_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["models", "list"]) == EXIT_CATALOG_ERROR
    assert "COGMESH_MODEL_CATALOG" in capsys.readouterr().err
    monkeypatch.setenv("COGMESH_MODEL_CATALOG", str(tmp_path / "gone.toml"))
    assert main(["models", "list"]) == EXIT_CATALOG_ERROR
    bad = tmp_path / "bad.toml"
    bad.write_text("[models.x]\nwire = 'ollama'\nsecret = 1\n", encoding="utf-8")
    monkeypatch.setenv("COGMESH_MODEL_CATALOG", str(bad))
    assert main(["models", "list"]) == EXIT_CATALOG_ERROR
    assert "model catalog" in capsys.readouterr().err
    monkeypatch.setenv("COGMESH_MODEL_CATALOG", str(_catalog(tmp_path, "http://127.0.0.1:9")))
    assert main(["models", "list"]) == EXIT_OK


def test_a_run_with_a_catalog_entry_records_calls_and_stats_show_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    with _server() as (url, seen):
        catalog = _catalog(tmp_path, url)
        assert _run(catalog, "local-compat", "--trace-db", str(db)) == EXIT_OK
    assert seen[0]["path"] == "/v1/chat/completions" and seen[0]["auth"] == ""
    capsys.readouterr()
    assert main(["models", "stats", "--db", str(db)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "1 run(s) in the database, 1 with model call records" in out
    assert "entry local-compat, local-compat/tiny-1  role code_explainer  task explain_file" in out
    assert "calls: 1 (ok 1)" in out and "prompt tokens: 120 over 1 ok call(s)" in out
    assert "completion tokens: 15" in out and "do not rank models" in out
    assert "best" not in out.lower()
    assert main(["models", "stats", "--db", str(db), "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    (group,) = data["groups"]
    assert group["calls"] == 1 and group["attempts_success"] == 1
    assert main(["models", "stats", "--db", str(db), "--task-type", "other_task"]) == EXIT_OK
    assert "no recorded model calls" in capsys.readouterr().out


def test_stats_counts_failures_and_unknown_tokens_separately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.sqlite3"
    no_usage = json.dumps(
        {"choices": [{"message": {"content": SUMMARY}, "finish_reason": "stop"}]}
    ).encode()
    with _server(body=no_usage) as (url, _):
        assert _run(_catalog(tmp_path, url), "local-compat", "--trace-db", str(db)) == EXIT_OK
    with _server(status=429, body=b"{}") as (url, _):
        code = _run(_catalog(tmp_path, url), "local-compat", "--trace-db", str(db))
    assert code == EXIT_TASK_NOT_SUCCEEDED
    capsys.readouterr()
    assert main(["models", "stats", "--db", str(db)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "calls: 3 (ok 1, rate_limited 2)" in out  # two attempts for the 429 run
    assert "prompt tokens: none reported (ok calls without a count: 1)" in out


def test_stats_with_a_missing_database_exits_8_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "none.sqlite3"
    assert main(["models", "stats", "--db", str(missing)]) == EXIT_TRACE_STORE_ERROR
    assert not missing.exists()


def test_a_remote_entry_without_opt_ins_exits_2_before_any_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUD_API_KEY", KEY_CANARY)
    catalog = _catalog(tmp_path, "http://127.0.0.1:9")
    assert _run(catalog, "cloud") == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "COGMESH_ALLOW_REMOTE_CONTENT" in err and KEY_CANARY not in err
    monkeypatch.setenv("COGMESH_ALLOW_REMOTE_CONTENT", "true")
    assert _run(catalog, "cloud") == EXIT_CONFIG_ERROR
    assert "COGMESH_ALLOW_TRAINING_USE" in capsys.readouterr().err


def test_an_unknown_entry_or_a_missing_catalog_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path, "http://127.0.0.1:9")
    assert _run(catalog, "nope") == EXIT_CONFIG_ERROR
    assert "no entry named nope" in capsys.readouterr().err
    code = main(
        [
            "run",
            str(FIXTURES / "plans" / "needs_model.json"),
            "--root",
            str(FIXTURES / "sample_app"),
            "--model",
            "local-compat",
        ]
    )
    assert code == EXIT_CONFIG_ERROR
    assert "COGMESH_MODEL_CATALOG" in capsys.readouterr().err


def test_a_plan_without_a_model_step_never_reads_the_catalog(tmp_path: Path) -> None:
    code = main(
        [
            "run",
            str(FIXTURES / "plans" / "ok.json"),
            "--root",
            str(FIXTURES / "dep_app"),
            "--model",
            "nope",
            "--catalog",
            str(tmp_path / "gone.toml"),
        ]
    )
    assert code == EXIT_OK


def test_the_planner_role_is_checked_for_plan_drafting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _server() as (url, _):
        catalog = _catalog(tmp_path, url)
        text = catalog.read_text(encoding="utf-8").replace(
            'roles = ["code_explainer", "planner"]', 'roles = ["code_explainer"]'
        )
        catalog.write_text(text, encoding="utf-8")
        code = main(
            ["plan", "list the imports", "--model", "local-compat", "--catalog", str(catalog)]
        )
    assert code == EXIT_CONFIG_ERROR
    assert "planner" in capsys.readouterr().err
