"""`experience outcome`, `experience promote` and `claims --outcomes` end to end (ADR-0018).

Each claim is judged against an empty directory (`blank`): nothing there re-derives the claim, so
only recorded outcomes and reviews can make it validated.
"""

import json
import shutil
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from cognitive_mesh.experience import NodeKind
from cognitive_mesh.experience import store as graph_store
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.outcomes import OutcomeStore

FIXTURES = Path(__file__).parent / "fixtures"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
A_B = claim_id("depends_on", A, B, "imports")


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    app = tmp_path / "app"
    shutil.copytree(FIXTURES / "dep_app", app)
    blank = tmp_path / "blank"
    blank.mkdir()
    paths = {
        "app": app,
        "blank": blank,
        "db": tmp_path / "t.sqlite3",
        "graph": tmp_path / "g.sqlite3",
        "outcomes": tmp_path / "o.sqlite3",
    }
    plan = FIXTURES / "plans" / "ok.json"
    assert main(["run", str(plan), "--root", str(app), "--trace-db", str(paths["db"])]) == 0
    assert (
        main(["experience", "build", "--db", str(paths["db"]), "--graph", str(paths["graph"])]) == 0
    )
    return paths


def cli(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    got = capsys.readouterr()
    return code, got.out, got.err


def record(
    capsys: pytest.CaptureFixture[str],
    world: dict[str, Path],
    source: str,
    result: str,
    claim: str = A_B,
) -> None:
    code, _, err = cli(
        capsys,
        "experience", "outcome", claim,
        "--source", source, "--result", result, "--detail", "pytest -q", "--by", "ci",
        "--root", world["blank"], "--graph", world["graph"], "--outcomes", world["outcomes"],
    )  # fmt: skip
    assert code == 0, err


def claim_status(capsys: pytest.CaptureFixture[str], world: dict[str, Path]) -> dict[str, object]:
    code, out, err = cli(
        capsys,
        "experience", "claims", "--db", world["db"], "--graph", world["graph"],
        "--root", world["blank"], "--outcomes", world["outcomes"], "--json",
    )  # fmt: skip
    assert code == 0, err
    rows = json.loads(out)["claims"]
    [row] = [r for r in rows if r["id"] == A_B]
    assert isinstance(row["assessment"], dict)
    return row["assessment"]


def promote(capsys: pytest.CaptureFixture[str], world: dict[str, Path]) -> tuple[int, str, str]:
    return cli(
        capsys,
        "experience", "promote", A_B, "--by", "ann",
        "--root", world["blank"], "--db", world["db"], "--graph", world["graph"],
        "--outcomes", world["outcomes"],
    )  # fmt: skip


def test_a_failed_check_makes_the_claim_contested_and_keeps_the_history(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "failed")
    got = claim_status(capsys, world)
    assert got["status"] == "contested" and got["outcome"] == "failed"
    assert (
        isinstance(got["outcomes"], list) and len(got["outcomes"]) == 1
    )  # the failure is still there after it is shown


def test_a_successful_check_validates_the_current_version(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "succeeded")
    got = claim_status(capsys, world)
    assert got["status"] == "validated" and got["validated_by"] == ["outcome"]


def test_an_ambiguous_outcome_is_unknown(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "ambiguous")
    assert claim_status(capsys, world)["status"] == "unknown"


def test_a_person_and_a_check_that_disagree_are_conflicting(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "succeeded")
    record(capsys, world, "person", "failed")
    assert claim_status(capsys, world)["status"] == "conflicting"


def test_a_refused_promotion_lists_the_unmet_checks_and_is_recorded(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = promote(capsys, world)
    assert code == 17
    assert "promotion refused" in out and "unmet:" in out
    assert "no successful outcome" in out
    with OutcomeStore.open_existing(world["outcomes"]) as store:
        [row] = store.list_outcomes(A_B)
    assert row.source.value == "promotion" and row.result.value == "rejected"


def test_each_refusal_links_to_the_one_before(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    promote(capsys, world)
    promote(capsys, world)
    with OutcomeStore.open_existing(world["outcomes"]) as store:
        first, second = store.list_outcomes(A_B)
    assert second.previous_id == first.id


def test_a_promotion_is_accepted_only_once_the_claim_is_validated(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "succeeded")
    code, out, _ = promote(capsys, world)
    assert code == 0 and "promotion accepted" in out
    with OutcomeStore.open_existing(world["outcomes"]) as store:
        assert all(o.source.value != "promotion" for o in store.list_outcomes(A_B))


def test_a_contested_claim_cannot_be_promoted(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    record(capsys, world, "check", "failed")
    code, out, _ = promote(capsys, world)
    assert code == 17 and "status is contested" in out


def test_an_outcome_for_an_unknown_node_exits_13_and_stores_nothing(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = cli(
        capsys,
        "experience", "outcome", A,
        "--source", "check", "--result", "failed", "--detail", "t", "--by", "ci",
        "--root", world["blank"], "--graph", world["graph"], "--outcomes", world["outcomes"],
    )  # fmt: skip
    assert code == 13 and "unknown node" in err
    assert not world["outcomes"].exists()


def test_a_control_character_in_the_detail_exits_16_and_stores_nothing(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = cli(
        capsys,
        "experience", "outcome", A_B,
        "--source", "check", "--result", "failed", "--detail", "t\x1b[31m", "--by", "ci",
        "--root", world["blank"], "--graph", world["graph"], "--outcomes", world["outcomes"],
    )  # fmt: skip
    assert code == 16 and "control characters" in err
    assert "\x1b" not in err


def test_claims_with_a_missing_outcomes_file_exits_16(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = cli(
        capsys,
        "experience", "claims", "--db", world["db"], "--graph", world["graph"],
        "--outcomes", world["blank"] / "nope.sqlite3",
    )  # fmt: skip
    assert code == 16 and "does not exist" in err


def test_a_refusal_creates_the_outcomes_file_when_it_is_missing(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    other = world["blank"] / "fresh.sqlite3"
    code, _, _ = cli(
        capsys,
        "experience", "promote", A_B, "--by", "ann", "--root", world["blank"],
        "--db", world["db"], "--graph", world["graph"], "--outcomes", other,
    )  # fmt: skip
    code, _, _ = cli(
        capsys,
        "experience", "promote", A_B, "--by", "ann", "--root", world["blank"],
        "--db", world["db"], "--graph", world["graph"], "--outcomes", other,
    )  # fmt: skip
    assert code == 17 and other.exists()


def test_an_outcome_for_a_node_that_is_not_a_claim_exits_13(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = graph_store.load(world["graph"]).snapshot
    [other] = [n for n in snapshot.nodes if n.kind is not NodeKind.CLAIM][:1]
    code, _, err = cli(
        capsys,
        "experience", "outcome", other.id,
        "--source", "check", "--result", "failed", "--detail", "t", "--by", "ci",
        "--root", world["blank"], "--graph", world["graph"], "--outcomes", world["outcomes"],
    )  # fmt: skip
    assert code == 13 and "not a claim" in err
    assert not world["outcomes"].exists()
