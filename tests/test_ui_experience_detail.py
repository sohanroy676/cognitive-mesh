"""One experience in full, and the preview in the list (ADR-0034)."""

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from cognitive_mesh.agents import AgentSuccess, Basis, EvidenceRef, ModelInfo
from cognitive_mesh.orchestrator import StepStatus
from cognitive_mesh.traces import (
    AttemptTrace,
    RunTrace,
    StepTrace,
    TraceEnvironment,
    TraceStatus,
    TraceStore,
)
from cognitive_mesh.ui import experiences as exp_module
from cognitive_mesh.ui.config import UiConfig
from memory_support import T0, add_run, failure, success
from ui_support import A_UTIL, World, digest, serving


def detail_url(doc_id: str) -> str:
    return "/api/experience?" + urllib.parse.urlencode({"id": doc_id})


def detail(c: Any, doc_id: str) -> dict[str, Any]:
    reply = c.get(detail_url(doc_id))
    assert reply.status == 200, reply.body
    data: dict[str, Any] = reply.json()["data"]
    return data


def rich_db(tmp_path: Path) -> Path:
    """A model-written step with evidence, a failed step, and a run with a code version."""
    db = tmp_path / "rich.sqlite3"
    modeled = AgentSuccess(
        agent_id="code_explainer",
        task_id="run-r-task.explain.1",
        capability="explain_file",
        output={"summary": "It adds two numbers.", "lines": [1, 2]},
        basis=Basis.MODEL_UNVERIFIED,
        evidence=(EvidenceRef(path="src/a.py", sha256="ab" * 32, line=3, rule="rule:x"),),
        model=ModelInfo(provider="ollama", model="llama3.2"),
    )
    add_run(
        db,
        "run-r",
        [
            ("explain", "explain_file", {"path": "src/a.py", "depth": 2}, modeled),
            (
                "deps",
                "dependencies_of",
                {"node_id": "module:a"},
                failure("run-r", "deps", "dependencies_of", "no provider"),
            ),
        ],
        fingerprint="f" * 40,
        app_id="demo@app",
    )
    return db


def add_retried_run(db: Path) -> None:
    """A step that failed once (retryable) and then succeeded: final attempt number 2."""
    first = failure("run-t", "s", "dependencies_of", "temporary")
    second = success("run-t", "s", "dependencies_of", {"ok": True}).model_copy(
        update={"task_id": "run-t-task.s.2"}
    )
    attempts = (
        AttemptTrace(attempt_no=1, agent_task_id="run-t-task.s.1", agent_id="agent", result=first),
        AttemptTrace(attempt_no=2, agent_task_id="run-t-task.s.2", agent_id="agent", result=second),
    )
    plan: dict[str, Any] = {
        "task_id": "run-t-task",
        "steps": [{"step_id": "s", "capability": "dependencies_of", "inputs": {}}],
    }
    header = RunTrace(
        run_id="run-t",
        task_id="run-t-task",
        status=TraceStatus.INTERRUPTED,
        started_at=T0,
        environment=TraceEnvironment(
            cognitive_mesh_version="t", python_version="3.12", platform="t"
        ),
        plan=plan,
    )
    step = StepTrace(
        seq=0,
        step_id="s",
        capability="dependencies_of",
        status=StepStatus.SUCCEEDED,
        agent_id="agent",
        attempt_count=2,
        attempts=attempts,
    )
    with TraceStore.create(db) as store:
        store.begin_run(header)
        store.finish_run("run-t", TraceStatus.SUCCEEDED, T0, (), (step,))


# ---- the preview in the list ------------------------------------------------------------------


def test_each_row_has_a_short_plain_preview(tmp_path: Path) -> None:
    with serving(UiConfig(db=rich_db(tmp_path), port=0)) as c:
        rows = {r["step_id"]: r for r in c.get("/api/experiences").json()["data"]["rows"]}
    assert rows["explain"]["preview"] == '{"lines":[1,2],"summary":"It adds two numbers."}'
    assert rows["deps"]["preview"] == "provider_unavailable: no provider"
    assert all(len(r["preview"]) <= 140 for r in rows.values())


def test_a_long_output_is_cut_to_140_characters_with_dots(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    out: Any = {"text": "word " * 100}
    add_run(
        db, "run-l", [("s", "dependencies_of", {}, success("run-l", "s", "dependencies_of", out))]
    )
    with serving(UiConfig(db=db, port=0)) as c:
        row = c.get("/api/experiences").json()["data"]["rows"][0]
    assert len(row["preview"]) == 140 and row["preview"].endswith("...")
    assert "\n" not in row["preview"]


# ---- the detail ------------------------------------------------------------------------------


def test_the_detail_equals_the_stored_record_field_by_field(tmp_path: Path) -> None:
    with serving(UiConfig(db=rich_db(tmp_path), port=0)) as c:
        d = detail(c, "run-r/explain/1")
    assert d["run"]["id"] == "run-r" and d["run"]["status"] == "failed"
    assert d["run"]["code_version"] == "f" * 40 and d["run"]["app_id"] == "demo@app"
    assert d["run"]["environment"]["python_version"] == "3.12" and d["run"]["redactions"] == []
    assert d["step"]["capability"] == "explain_file" and d["step"]["attempt_count"] == 1
    assert json.loads(d["inputs"]["text"]) == {"path": "src/a.py", "depth": 2}
    a = d["attempt"]
    assert a["status"] == "success" and a["basis"] == "model_unverified"
    assert a["agent_id"] == "code_explainer"
    assert json.loads(a["output"]["text"]) == {"summary": "It adds two numbers.", "lines": [1, 2]}
    assert a["model"] == {"provider": "ollama", "model": "llama3.2"}
    assert a["evidence"] == [{"path": "src/a.py", "sha256": "ab" * 32, "line": 3, "rule": "rule:x"}]
    assert d["claims"] is None and "never run" in d["note"]


def test_a_failed_step_shows_why_it_failed_and_has_no_output(tmp_path: Path) -> None:
    with serving(UiConfig(db=rich_db(tmp_path), port=0)) as c:
        a = detail(c, "run-r/deps/1")["attempt"]
    assert a["status"] == "failure" and a["kind"] == "provider_unavailable"
    assert a["message"] == "no provider" and a["retryable"] is True
    assert "output" not in a and "model" not in a


def test_a_retried_step_lists_its_other_attempts(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_retried_run(db)
    with serving(UiConfig(db=db, port=0)) as c:
        d = detail(c, "run-t/s/2")
        assert d["step"]["attempt_count"] == 2 and d["attempt"]["attempt_no"] == 2
        assert d["other_attempts"] == [
            {"attempt_no": 1, "outcome": "failure", "failure_kind": "provider_unavailable"}
        ]
        # the first attempt is not the step's experience: only the final one is
        assert c.get(detail_url("run-t/s/1")).status == 404


def test_a_part_longer_than_100000_characters_is_cut_and_says_how_much(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    out: Any = {"blob": "y" * 300000}
    add_run(
        db,
        "run-b",
        [
            (
                "s",
                "dependencies_of",
                {"big": "z" * 250000},
                success("run-b", "s", "dependencies_of", out),
            )
        ],
    )
    with serving(UiConfig(db=db, port=0)) as c:
        d = detail(c, "run-b/s/1")
    full_output = len(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    assert len(d["attempt"]["output"]["text"]) == exp_module.PART_CHARS
    assert d["attempt"]["output"]["hidden_chars"] == full_output - exp_module.PART_CHARS
    assert d["inputs"]["hidden_chars"] > 0
    small = rich_db(tmp_path)
    with serving(UiConfig(db=small, port=0)) as c:
        assert detail(c, "run-r/explain/1")["attempt"]["output"]["hidden_chars"] == 0


def test_markup_in_stored_text_comes_back_as_inert_json(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    step = "<b>s</b>&'"
    out: Any = {"note": evil}
    add_run(
        db,
        "run-h",
        [(step, "dependencies_of", {"in": evil}, success("run-h", step, "dependencies_of", out))],
    )
    with serving(UiConfig(db=db, port=0)) as c:
        reply = c.get(detail_url(f"run-h/{step}/1"))
    assert reply.status == 200 and reply.headers["content-type"] == "application/json"
    assert reply.headers["x-content-type-options"] == "nosniff"
    data = reply.json()["data"]
    assert evil in data["attempt"]["output"]["text"] and evil in data["inputs"]["text"]
    assert data["step"]["step_id"] == step


def test_a_step_id_with_slashes_is_found(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    step = "group/part"
    add_run(
        db, "run-s", [(step, "dependencies_of", {}, success("run-s", step, "dependencies_of", {}))]
    )
    with serving(UiConfig(db=db, port=0)) as c:
        assert detail(c, f"run-s/{step}/1")["step"]["step_id"] == step


@pytest.mark.parametrize(
    ("doc_id", "status"),
    [
        ("run-r/explain/2", 404),
        ("run-r/nope/1", 404),
        ("no-such-run/explain/1", 404),
        ("run-r", 400),
        ("run-r/explain", 400),
        ("run-r/explain/x", 400),
        ("/explain/1", 400),
    ],
)
def test_ids_that_are_not_experiences_are_refused_with_the_right_status(
    tmp_path: Path, doc_id: str, status: int
) -> None:
    with serving(UiConfig(db=rich_db(tmp_path), port=0)) as c:
        reply = c.get(detail_url(doc_id))
        assert reply.status == status, reply.body
        assert "error" in reply.json()


def test_an_unfinished_runs_step_is_not_an_experience(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite3"
    add_run(
        db,
        "run-u",
        [("s", "dependencies_of", {}, success("run-u", "s", "dependencies_of", {}))],
        finish=False,
    )
    with serving(UiConfig(db=db, port=0)) as c:
        assert c.get(detail_url("run-u/s/1")).status == 404


def test_an_unreadable_run_and_a_missing_database_are_conflicts(tmp_path: Path) -> None:
    db = rich_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET status = 'succeeded' WHERE run_id = 'run-r'")
    conn.commit()
    conn.close()
    with serving(UiConfig(db=db, port=0)) as c:
        assert c.get(detail_url("run-r/explain/1")).status == 409
    with serving(UiConfig(port=0)) as c:
        reply = c.get(detail_url("run-r/explain/1"))
        assert reply.status == 409 and "--db" in str(reply.json()["error"])
        assert c.get("/api/experience").status == 400  # the id is required


def test_claims_appear_with_a_graph_and_only_then(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        rows = c.get("/api/experiences").json()["data"]["rows"]
        deps = next(r for r in rows if r["capability"] == "dependencies_of")
        with_graph = detail(c, deps["id"])
    assert any(cl["id"] == A_UTIL for cl in with_graph["claims"])
    with serving(ui_world.config(graph=None)) as c:
        assert detail(c, deps["id"])["claims"] is None


def test_reading_an_experience_writes_nothing(ui_world: World) -> None:
    before = digest(ui_world.files())
    with serving(ui_world.config()) as c:
        for row in c.get("/api/experiences").json()["data"]["rows"]:
            assert c.get(detail_url(row["id"])).status == 200
    assert digest(ui_world.files()) == before


def test_every_listed_experience_can_be_opened(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        rows = c.get("/api/experiences").json()["data"]["rows"]
        assert rows and all(c.get(detail_url(r["id"])).status == 200 for r in rows)
