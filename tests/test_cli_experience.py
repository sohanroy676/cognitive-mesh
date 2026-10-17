"""The `experience` commands (ADR-0015)."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from experience_support import deps_step, failed_step, make_db

A, B, C = "module:a.py", "module:b.py", "module:c.py"


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    db = make_db(
        tmp_path,
        {
            "r1": ([deps_step("r1", "s1", A, [B, C])], "fp1"),
            "r2": ([deps_step("r2", "s1", A, [B])], "fp2"),
        },
    )
    return db, tmp_path / "graph.sqlite3"


def run(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def build(capsys: pytest.CaptureFixture[str], db: Path, graph: Path) -> None:
    code, _, err = run(capsys, "experience", "build", "--db", db, "--graph", graph)
    assert code == 0, err


def sql(path: Path, *statements: str) -> None:
    conn = sqlite3.connect(path)
    try:
        for s in statements:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def test_build_reports_counts_and_writes_the_file(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    code, out, _ = run(capsys, "experience", "build", "--db", db, "--graph", graph)
    assert code == 0 and graph.is_file()
    assert "2 claims (all unvalidated)" in out and "content sha256:" in out


def test_build_json_shape(world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    db, graph = world
    code, out, _ = run(capsys, "experience", "build", "--db", db, "--graph", graph, "--json")
    data = json.loads(out)
    assert code == 0
    assert set(data) == {"content_sha256", "nodes", "edges", "diagnostics"}
    assert data["nodes"]["claim"] == 2 and data["diagnostics"] == []


def test_build_skips_a_malformed_run_exits_8_and_still_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = make_db(
        tmp_path,
        {"bad": ([failed_step("bad", "s1")], None), "ok": ([deps_step("ok", "s1", A, [B])], None)},
    )
    sql(db, "UPDATE runs SET status = 'succeeded' WHERE run_id = 'bad'")
    graph = tmp_path / "g.sqlite3"
    code, out, err = run(capsys, "experience", "build", "--db", db, "--graph", graph)
    assert code == 8 and "malformed" in err and "malformed_run" in out
    assert graph.is_file()


def test_build_with_a_missing_trace_database_exits_8_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = tmp_path / "g.sqlite3"
    code, _, err = run(
        capsys, "experience", "build", "--db", tmp_path / "none.sqlite3", "--graph", graph
    )
    assert code == 8 and "does not exist" in err
    assert not graph.exists() and not (tmp_path / "none.sqlite3").exists()


def test_build_into_a_missing_directory_exits_12(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    code, _, err = run(
        capsys, "experience", "build", "--db", db, "--graph", graph.parent / "no" / "g.sqlite3"
    )
    assert code == 12 and err


def test_claims_show_counts_and_the_unvalidated_note(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    code, out, _ = run(capsys, "experience", "claims", "--db", db, "--graph", graph)
    assert code == 0
    assert f"observed 2x (2 still confirmed, 2 code version(s))  {A} depends_on {B}" in out
    assert f"observed 1x (1 still confirmed, 1 code version(s))  {A} depends_on {C}" in out
    assert "not proof of truth" in out


def test_claims_json(world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    db, graph = world
    build(capsys, db, graph)
    _, out, _ = run(capsys, "experience", "claims", "--db", db, "--graph", graph, "--json")
    data = json.loads(out)
    assert {c["validation"] for c in data["claims"]} == {"unvalidated"}
    ab = next(c for c in data["claims"] if c["name"].endswith(f"depends_on {B}"))
    assert ab["observations"] == 2 and ab["source_fingerprints"] == ["fp1", "fp2"]
    assert data["hidden_unverified"] == 0 and "not proof" in data["note"]


def test_show_follows_edges_and_unknown_nodes_exit_13(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    code, out, _ = run(capsys, "experience", "show", "run:r1", "--db", db, "--graph", graph)
    assert code == 0 and "depth 1 via has_step: step:r1/s1" in out
    code, out, _ = run(
        capsys, "experience", "show", "run:r1", "--db", db, "--graph", graph, "--transitive"
    )
    assert "attempt:r1/s1/1" in out and "claim:" not in out  # claims point at attempts, not back
    code, _, err = run(capsys, "experience", "show", "nope", "--db", db, "--graph", graph)
    assert code == 13 and "unknown node" in err


def test_show_incoming_finds_the_claims_resting_on_a_target(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    code, out, _ = run(
        capsys, "experience", "show", f"target:{C}", "--db", db, "--graph", graph, "--incoming"
    )
    assert code == 0 and "via object: claim:" in out


def test_hostile_node_ids_cannot_drive_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evil = "module:a\x1b[31m\nFAKE LINE.py"
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", evil, [B])], None)})
    graph = tmp_path / "g.sqlite3"
    build(capsys, db, graph)
    for argv in (("claims",), ("show", f"target:{evil}", "--incoming")):
        code, out, err = run(capsys, "experience", *argv, "--db", db, "--graph", graph)
        assert code == 0 and "\x1b" not in out + err and "\nFAKE LINE" not in out
        assert "\\x1b[31m\\nFAKE LINE" in out  # shown, but escaped


def test_check_passes_on_a_clean_database(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    code, out, err = run(capsys, "experience", "check", "--db", db, "--graph", graph)
    assert code == 0 and "content hash matches" in out and "source_missing" not in out + err


def test_a_deleted_run_is_reported_hidden_and_shown_on_request(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    sql(
        db,
        "DELETE FROM attempts WHERE run_id = 'r1'",
        "DELETE FROM steps WHERE run_id = 'r1'",
        "DELETE FROM runs WHERE run_id = 'r1'",
    )
    code, out, err = run(capsys, "experience", "check", "--db", db, "--graph", graph)
    assert code == 14
    assert "source_missing=" in out and "diagnostic source_missing: run:r1" in err
    _, out, _ = run(capsys, "experience", "claims", "--db", db, "--graph", graph)
    assert f"{A} depends_on {C}" not in out  # only r1 stated it, and r1 is gone
    assert "1 claim(s) hidden" in out
    assert "observed 2x (1 still confirmed" in out  # r2 still supports A -> B
    _, out, _ = run(
        capsys, "experience", "claims", "--db", db, "--graph", graph, "--include-unverified"
    )
    assert (
        f"source_missing  observed 1x (0 still confirmed, 1 code version(s))  {A} depends_on {C}"
        in out
    )
    _, out, _ = run(capsys, "experience", "show", "run:r1", "--db", db, "--graph", graph)
    assert "status=source_missing" in out and "hidden because" in out
    _, out, _ = run(
        capsys, "experience", "show", "run:r1", "--db", db, "--graph", graph, "--include-unverified"
    )
    assert "step:r1/s1" in out


def test_a_tampered_graph_file_exits_12(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    build(capsys, db, graph)
    sql(graph, "UPDATE meta SET value = 'x' WHERE key = 'content_sha256'")
    for cmd in ("check", "claims", "show"):
        extra = ("run:r1",) if cmd == "show" else ()
        code, _, err = run(capsys, "experience", cmd, *extra, "--db", db, "--graph", graph)
        assert code == 12 and "hash does not match" in err


def test_a_missing_graph_file_exits_12_and_is_not_created(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, graph = world
    code, _, err = run(capsys, "experience", "check", "--db", db, "--graph", graph)
    assert code == 12 and "does not exist" in err and not graph.exists()


def test_the_graph_is_available_in_a_fresh_process(
    world: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    db, graph = world
    build(capsys, db, graph)
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "cognitive_mesh",
            "experience",
            "claims",
            "--db",
            str(db),
            "--graph",
            str(graph),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert len(json.loads(done.stdout)["claims"]) == 2
