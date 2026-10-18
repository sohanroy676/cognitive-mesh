"""`recall` and `task` read the outcomes file, so outcomes and their expiry reach the pack."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import test_cli_recall as base
from cognitive_mesh.experience.current import observe_current
from cognitive_mesh.experience.outcomes import OutcomeResult, OutcomeSource, OutcomeStore
from test_cli_recall import (
    A_UTIL,
    FIXTURES,
    World,
    claim_in,
    out_of,
    recall,
    recall_json,
    run,
    status_of,
)

# the fixtures of the recall tests, reused here by name
world = base.world
fake_embedder = base.fake_embedder

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def outcomes_file(
    w: World, result: OutcomeResult, *, until: date | None = None, now: datetime | None = None
) -> Path:
    path = w.db.parent / "o.sqlite3"
    fingerprint = observe_current(w.app).fingerprint
    with OutcomeStore.create(path) as store:
        store.add(
            A_UTIL, fingerprint, OutcomeSource.PERSON, result, "d", "me", now=now, valid_until=until
        )
    return path


def test_without_an_outcomes_file_the_claim_is_validated_by_rederivation(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    data = recall_json(world, capsys, "--root", world.app)
    assert status_of(claim_in(data, A_UTIL)) == "validated"


def test_a_failed_outcome_reaches_recall(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    path = outcomes_file(world, OutcomeResult.FAILED)
    data = recall_json(world, capsys, "--root", world.app, "--outcomes", path)
    assert status_of(claim_in(data, A_UTIL)) == "contested"


def test_an_expired_outcome_makes_the_claim_expired_in_recall(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    path = outcomes_file(world, OutcomeResult.SUCCEEDED, until=date(2020, 6, 1), now=LONG_AGO)
    data = recall_json(world, capsys, "--root", world.app, "--outcomes", path)
    assert status_of(claim_in(data, A_UTIL)) == "expired"


def test_a_missing_outcomes_file_exits_16_and_creates_nothing(
    world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.sqlite3"
    assert run(*recall(world, "--outcomes", missing)) == 16
    assert out_of(capsys)[1]
    assert not missing.exists()


def test_task_refuses_a_missing_outcomes_file(
    world: World, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.sqlite3"
    code = run(
        "task", "dependencies of module a.py", "--plan", FIXTURES / "plans" / "ok.json",
        "--root", world.app, "--db", world.db, "--index", world.idx, "--graph", world.graph,
        "--outcomes", missing,
    )  # fmt: skip
    assert code == 16
    assert not missing.exists()
