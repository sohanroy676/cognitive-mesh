"""`task --conduct`: a conductor drafts and routes the plan, every part is recorded (ADR-0040)."""

import json
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import (
    EXIT_CONDUCTOR_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_TASK_NOT_SUCCEEDED,
    main,
)
from cognitive_mesh.decisions.models import DecisionBasis, MadeBy
from cognitive_mesh.orchestrator.conduct import Swap
from cognitive_mesh.providers import ModelClient, ProviderError, ProviderErrorKind
from cognitive_mesh.providers.recording import CONDUCTOR_ROLE, CallResult
from cognitive_mesh.traces import TraceStore
from conductor_support import (
    Fleet,
    ScriptedClient,
    reply,
    write_catalog,
)

FIXTURES = Path(__file__).parent / "fixtures"
APP = FIXTURES / "sample_app"
REQUEST = "explain the project file"


class Setup:
    def __init__(self, tmp: Path, mp: pytest.MonkeyPatch, conductor: ScriptedClient, fleet: Fleet):
        self.tmp = tmp
        self.catalog = write_catalog(tmp)
        self.db = tmp / "t.sqlite3"
        self.conductor = conductor
        self.fleet = fleet
        mp.setattr(cli, "build_model_client", self._conductor_client)
        mp.setattr("cognitive_mesh.providers.routing.build_entry_client", fleet)

    def _conductor_client(self, settings: object, roles: object = ()) -> ModelClient:
        return self.conductor

    def args(self, *extra: str | Path) -> list[str]:
        base = [
            "task",
            REQUEST,
            "--conduct",
            "--conductor",
            "alpha",
            "--catalog",
            str(self.catalog),
            "--root",
            str(APP),
            "--db",
            str(self.db),
            "--index",
            str(self.tmp / "no-index"),
            "--graph",
            str(self.tmp / "no-graph.sqlite3"),
        ]
        return [*base, *(str(e) for e in extra)]


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    return Setup(tmp_path, monkeypatch, ScriptedClient(reply(), repeat=True), Fleet({}))


def only_run(db: Path) -> str:
    with TraceStore.open_existing(db) as store:
        (summary,) = store.list().runs
    return summary.run_id


def test_a_conducted_task_runs_and_records_plan_decisions_and_calls(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(setup.args()) == EXIT_OK
    out = capsys.readouterr().out
    assert "conductor plan (entry alpha; model-written and unreviewed" in out
    assert "role=code_explainer model=alpha" in out
    assert "evidence: insufficient_evidence: 0 call(s) with a known outcome of 0" in out
    assert "exploration: off" in out and "trace: " in out
    run_id = only_run(setup.db)
    with TraceStore.open_existing(setup.db) as store:
        decisions = [d for d in store.read_decisions().decisions if d.run_id == run_id]
        calls = [r for r in store.call_rows().rows if r.run_id == run_id]
        trace = store.get(run_id)
    plan, step = decisions
    assert (plan.step_id, step.step_id) == (None, "explain")
    assert all(
        d.made_by is MadeBy.MODEL and d.basis is DecisionBasis.MODEL_UNVERIFIED for d in decisions
    )
    assert all(d.role == CONDUCTOR_ROLE and d.entry_id == "alpha" for d in decisions)
    assert plan.agent_task_id == step.agent_task_id == "conducted._conductor.1"
    assert [c.target for c in step.citations if c.kind.value == "decision"] == [plan.decision_id]
    assert step.chosen == "alpha" and "Assign alpha to role code_explainer" in step.summary
    assert trace.status.value == "succeeded"
    by_role = {r.call.role: r for r in calls}
    assert by_role[CONDUCTOR_ROLE].call.step_id is None
    assert by_role[CONDUCTOR_ROLE].step_status is None
    assert by_role["code_explainer"].call.entry_id == "alpha"
    assert by_role["code_explainer"].attempt_status == "success"


def test_the_stored_run_reads_back_in_a_new_command(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(setup.args()) == EXIT_OK
    capsys.readouterr()
    run_id = only_run(setup.db)
    assert main(["traces", "show", run_id, "--db", str(setup.db)]) == EXIT_OK
    shown = capsys.readouterr().out
    assert (
        "conductor decisions (model-written, model_unverified; reasons are not evidence)" in shown
    )
    assert "plan:" in shown and "step explain:" in shown and "model calls:" in shown
    assert main(["decisions", "list", "--db", str(setup.db)]) == EXIT_OK
    assert "Assign alpha to role code_explainer" in capsys.readouterr().out
    assert main(["models", "stats", "--db", str(setup.db)]) == EXIT_OK
    assert "conductor" in capsys.readouterr().out


def test_json_output_carries_the_assignments_and_the_decision_ids(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(setup.args("--json")) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    conductor = payload["conductor"]
    assert conductor["reasoning_basis"] == "model_unverified" and len(conductor["decisions"]) == 2
    (row,) = conductor["assignments"]
    assert row["model"] == "alpha" and row["evidence"]["label"] == "insufficient_evidence"
    assert row["reason_basis"] == "model_unverified"


def test_a_rate_limited_model_hands_over_to_the_next_entry_and_both_calls_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = Fleet({"alpha": [ProviderError(ProviderErrorKind.RATE_LIMITED)]})
    s = Setup(tmp_path, monkeypatch, ScriptedClient(reply(), repeat=True), fleet)
    assert main(s.args()) == EXIT_OK
    out = capsys.readouterr().out
    assert "model switch in conducted.explain.1: alpha -> beta after rate_limited" in out
    with TraceStore.open_existing(s.db) as store:
        rows = [r.call for r in store.call_rows().rows if r.call.role == "code_explainer"]
    assert [(c.entry_id, c.result) for c in rows] == [
        ("alpha", CallResult.RATE_LIMITED),
        ("beta", CallResult.OK),
    ]
    assert main(["traces", "show", only_run(s.db), "--db", str(s.db)]) == EXIT_OK
    assert "switched from alpha to beta after rate_limited" in capsys.readouterr().out


def test_every_entry_limited_fails_the_step_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    limited = ProviderError(ProviderErrorKind.RATE_LIMITED)
    fleet = Fleet({"alpha": [limited] * 2, "beta": [limited] * 2, "gamma": [limited] * 2})
    s = Setup(tmp_path, monkeypatch, ScriptedClient(reply(), repeat=True), fleet)
    assert main(s.args()) == EXIT_TASK_NOT_SUCCEEDED
    capsys.readouterr()
    with TraceStore.open_existing(s.db) as store:
        trace = store.get(only_run(s.db))
        calls = [r.call for r in store.call_rows().rows if r.call.role == "code_explainer"]
    assert trace.status.value == "failed"
    assert calls and all(c.result is CallResult.RATE_LIMITED for c in calls)
    assert {c.entry_id for c in calls} == {"alpha", "beta", "gamma"}


def test_an_unusable_conductor_plan_runs_nothing_and_exits_22(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = reply(assignments={"explain": {"model": "ghost", "reason": "r"}})
    s = Setup(tmp_path, monkeypatch, ScriptedClient(bad, repeat=True), Fleet({}))
    assert main(s.args()) == EXIT_CONDUCTOR_ERROR
    err = capsys.readouterr().err
    assert "conductor: the conductor's plan was rejected" in err and "not an entry" in err
    assert "nothing was run" in err
    with TraceStore.open_existing(s.db) as store:
        assert store.list().runs == ()
        assert store.read_decisions().decisions == ()
    assert s.fleet.clients == {}


def test_a_conductor_provider_failure_exits_22(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(
        tmp_path,
        monkeypatch,
        ScriptedClient(ProviderError(ProviderErrorKind.TIMEOUT), repeat=True),
        Fleet({}),
    )
    assert main(s.args()) == EXIT_CONDUCTOR_ERROR
    assert "timeout" in capsys.readouterr().err


def test_exploration_moves_the_step_and_the_decision_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(tmp_path, monkeypatch, ScriptedClient(reply(), repeat=True), Fleet({}))
    monkeypatch.setattr(
        "cognitive_mesh.orchestrator.conduct.explore",
        lambda *a, **k: Swap("explain", "alpha", "gamma", 0.2),
    )
    assert main(s.args("--explore-rate", "0.2")) == EXIT_OK
    out = capsys.readouterr().out
    assert "conductor chose alpha; explored" in out
    assert "exploration: rate 0.2, explain moved from alpha to gamma" in out
    with TraceStore.open_existing(s.db) as store:
        step = next(d for d in store.read_decisions().decisions if d.step_id == "explain")
        calls = [r.call for r in store.call_rows().rows if r.call.role == "code_explainer"]
    assert step.chosen == "gamma" and "exploration moved it from alpha (rate 0.2)" in step.summary
    assert step.reasoning.startswith("[exploration swap from alpha to gamma]")
    assert [c.entry_id for c in calls] == ["gamma"]


def test_the_default_never_explores(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cognitive_mesh.orchestrator.conduct.explore",
        lambda run_id, rate, *a, **k: (
            pytest.fail(f"asked to explore at rate {rate}") if rate else None
        ),
    )
    assert main(setup.args()) == EXIT_OK
    assert list(setup.fleet.clients) == ["alpha"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--plan", "p.json"],  # --conduct and --plan
        ["--model", "alpha"],  # --model with --conduct
        ["--workspace", "w.sqlite3"],  # --workspace without --project
        ["--project", "p"],
    ],
)
def test_conflicting_flags_are_a_usage_error(
    setup: Setup, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(setup.args(*argv)) == EXIT_CONFIG_ERROR
    assert capsys.readouterr().err.strip()
    assert not setup.db.exists()


def test_conduct_needs_a_conductor_and_conductor_flags_need_conduct(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    args = setup.args()
    without_conductor = list(args)
    at = without_conductor.index("--conductor")
    del without_conductor[at : at + 2]
    assert main(without_conductor) == EXIT_CONFIG_ERROR
    assert "--conduct needs --conductor" in capsys.readouterr().err
    without_conduct = [a for a in args if a != "--conduct"]
    assert main([*without_conduct, "--plan", "p.json"]) == EXIT_CONFIG_ERROR
    assert "--conductor, --workspace, --project and --explore-rate need --conduct" in (
        capsys.readouterr().err
    )
    neither = [a for a in without_conduct if a not in ("--conductor", "alpha")]
    assert main(neither) == EXIT_CONFIG_ERROR
    assert "give --plan FILE, or --conduct" in capsys.readouterr().err


def test_an_explore_rate_above_the_limit_is_refused_by_the_parser(setup: Setup) -> None:
    with pytest.raises(SystemExit) as caught:
        main(setup.args("--explore-rate", "0.5"))
    assert caught.value.code == 2


def test_a_conductor_entry_without_the_role_is_a_config_error(
    setup: Setup, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()  # use the real build_model_client: beta does not list the role
    args = setup.args()
    args[args.index("alpha")] = "beta"
    assert main(args) == EXIT_CONFIG_ERROR
    assert "conductor" in capsys.readouterr().err
