"""A question gets an answer, and each agent's result is shown (demo-Web-app)."""

import json
from pathlib import Path
from typing import Any

from cognitive_mesh.providers import ModelRequest, ModelResponse
from conductor_support import EXPLAIN_OK, reply
from impact_support import make_workspace  # noqa: F401 - used through test_serve_flow helpers
from serve_support import ANALYSIS_ASSIGN, ANALYSIS_STEPS, Models, config_for, running, wait_for
from test_serve_flow import get, onboard, send, status_of


class AnswerWorker:
    provider = "fake"
    model = "m"

    """Explains files as before; answers the final question as the answerer role would."""

    def __init__(self, answer_reply: str) -> None:
        self.answer_reply = answer_reply
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = self.answer_reply if "answer one question" in request.system else EXPLAIN_OK
        return ModelResponse(provider="fake", model="m", text=text)


def run_question(
    tmp_path: Path, answer_reply: str
) -> tuple[Any, AnswerWorker, list[dict[str, Any]]]:
    models = Models(reply(ANALYSIS_STEPS, ANALYSIS_ASSIGN, "read one file"))
    worker = AnswerWorker(answer_reply)
    models.worker = worker
    with running(config_for(tmp_path, models)) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "what does app.py do", mode="ask")
        assert wait_for(lambda: status_of(s, turn) in ("done", "failed")), "stuck"
        assert status_of(s, turn) == "done", get(s.app, f"/api/turn?id={turn}").json["events"]
        events = get(s.app, f"/api/turn?id={turn}").json["events"]
    return s, worker, events


def test_the_question_gets_an_answer_citing_files_it_was_given(tmp_path: Path) -> None:
    good = json.dumps({"answer": "It prints a greeting.", "cited_paths": ["app.py"]})
    _s, worker, events = run_question(tmp_path, good)
    answer = next(e["data"] for e in events if e["kind"] == "answer")
    assert answer["text"] == "It prints a greeting."
    assert answer["cited_paths"] == ["app.py"] and answer["basis"] == "model_unverified"
    final = [r for r in worker.requests if "answer one question" in r.system][0]
    body = json.loads(final.prompt)
    assert body["question"] == "what does app.py do"
    assert [f["path"] for f in body["files"]] == ["app.py"]
    assert body["findings"]  # what the earlier step found


def test_each_step_shows_what_it_did(tmp_path: Path) -> None:
    good = json.dumps({"answer": "ok", "cited_paths": []})
    _s, _w, events = run_question(tmp_path, good)
    step = next(e["data"] for e in events if e["kind"] == "analysis")["steps"][0]
    assert step["inputs"] == {"path": "app.py"} and step["attempts"] == 1
    assert step["model"] == "fake/m" and step["output"]


def test_a_citation_that_was_not_provided_is_not_shown_as_an_answer(tmp_path: Path) -> None:
    bad = json.dumps({"answer": "x", "cited_paths": ["secret.py"]})
    _s, _w, events = run_question(tmp_path, bad)
    kinds = [e["kind"] for e in events]
    assert "answer" not in kinds and "answer_failed" in kinds and "done" in kinds
    failed = next(e["data"] for e in events if e["kind"] == "answer_failed")
    assert "cited a file that was not provided" in failed["message"]


def test_a_model_that_does_not_return_json_fails_the_answer_not_the_turn(tmp_path: Path) -> None:
    _s, _w, events = run_question(tmp_path, "sure, it prints hello")
    assert [e["kind"] for e in events if e["kind"] in ("answer", "answer_failed", "done")] == [
        "answer_failed",
        "done",
    ]


def test_the_next_model_answers_when_the_first_is_limited(tmp_path: Path) -> None:
    from cognitive_mesh.providers import ProviderError, ProviderErrorKind

    class Limited(AnswerWorker):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if "answer one question" in request.system:
                raise ProviderError(ProviderErrorKind.RATE_LIMITED, "limited")
            return super().complete(request)

    models = Models(reply(ANALYSIS_STEPS, ANALYSIS_ASSIGN, "read one file"))
    models.worker = Limited("")
    second = AnswerWorker(json.dumps({"answer": "second model", "cited_paths": []}))
    catalog_extra = SECOND_ENTRY
    config = config_for(tmp_path, models)
    assert config.catalog is not None
    config.catalog.write_text(config.catalog.read_text(encoding="utf-8") + catalog_extra)
    original = models.__call__

    def route(entry_id: str) -> Any:
        return second if entry_id == "second" else original(entry_id)

    object.__setattr__(config, "client_factory", route)
    with running(config) as s:
        session = onboard(s, tmp_path)
        turn = send(s, session, "what does app.py do", mode="ask")
        assert wait_for(lambda: status_of(s, turn) == "done")
        events = get(s.app, f"/api/turn?id={turn}").json["events"]
    answer = next(e["data"] for e in events if e["kind"] == "answer")
    assert answer["text"] == "second model" and answer["model"] == "second"


SECOND_ENTRY = """
[models.second]
wire = "ollama"
base_url = "http://localhost:11434"
model = "llama3.2"
context_tokens = 8192
tier = "free"
data_use = "no_training"
roles = ["code_explainer"]
"""
