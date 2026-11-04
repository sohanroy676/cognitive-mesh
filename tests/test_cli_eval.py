"""The `eval` commands through main() (ADR-0043)."""

import json
from pathlib import Path

import pytest

from cognitive_mesh import cli
from cognitive_mesh.cli import main
from cognitive_mesh.evaluate import cli as eval_cli
from cognitive_mesh.evaluate.runner import run_evaluation
from eval_support import Script, factory_for, make_set, settings
from memory_support import FakeEmbedder


def test_exit_code_is_pinned() -> None:
    assert cli.EXIT_EVAL_ERROR == eval_cli.EXIT_EVAL_ERROR == 25


@pytest.fixture
def finished(tmp_path: Path) -> Path:
    ws, ts = make_set(tmp_path)
    out = tmp_path / "out"
    run_evaluation(
        ts,
        settings(out),
        registry_factory=factory_for(Script()),
        embedder_factory=FakeEmbedder,
    )
    return out


def test_report_text_and_json(finished: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "report", str(finished)]) == 0
    text = capsys.readouterr().out
    assert "too few paired tasks to conclude anything" in text
    assert "memory off" in text and "memory on" in text
    assert "validates only that the declared command passed" in text
    assert main(["eval", "report", str(finished), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["paired"]["counted"] == 2 and len(data["items"]) == 4


def test_report_of_a_missing_or_foreign_folder_exits_25(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["eval", "report", str(tmp_path / "nope")]) == 25
    assert "does not exist" in capsys.readouterr().err
    assert not (tmp_path / "nope").exists()  # reading never creates anything
    (tmp_path / "eval.sqlite3").write_bytes(b"not sqlite at all, but long enough to be a file")
    assert main(["eval", "report", str(tmp_path)]) == 25


def test_report_unknown_eval_id_exits_25(finished: Path) -> None:
    assert main(["eval", "report", str(finished), "--eval-id", "99"]) == 25


def test_run_with_a_bad_taskset_exits_25_before_touching_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("nonsense [", encoding="utf-8")
    catalog = tmp_path / "catalog.toml"
    catalog.write_text("", encoding="utf-8")
    out = tmp_path / "out"
    args = ["eval", "run", str(bad), "--out", str(out), "--model", "m1", "--catalog", str(catalog)]
    assert main(args) == 25
    assert not out.exists()


def run_args(tmp_path: Path, catalog: Path) -> list[str]:
    return [
        "eval",
        "run",
        str(tmp_path / "tasks.toml"),
        "--out",
        str(tmp_path / "out"),
        "--model",
        "nope",
        "--catalog",
        str(catalog),
    ]


def test_run_with_a_missing_catalog_file_exits_25(tmp_path: Path) -> None:
    make_set(tmp_path)
    assert main(run_args(tmp_path, tmp_path / "missing-catalog.toml")) == 25
    assert not (tmp_path / "out").exists()


def test_run_with_an_unknown_catalog_entry_exits_with_a_config_code(tmp_path: Path) -> None:
    make_set(tmp_path)
    catalog = tmp_path / "catalog.toml"
    catalog.write_text("", encoding="utf-8")
    assert main(run_args(tmp_path, catalog)) in (2, 20)  # never a traceback
    assert not (tmp_path / "out").exists()
