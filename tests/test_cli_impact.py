"""`workspace impact`, `workspace contract-diff` and `run --workspace` (ADR-0041)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cognitive_mesh.cli import main
from impact_support import Workspace, commit_all, edit_file, git, make_workspace


def run(capsys: pytest.CaptureFixture[str], *argv: str | Path) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return make_workspace(tmp_path)


def test_impact_text_shows_dependents_with_evidence(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(
        capsys, "workspace", "impact", "api", "--target", "route:GET /users", "--workspace", ws.file
    )
    assert code == 0, err
    assert "impact of route:GET /users in api" in out
    assert "web: [api] GET /users (exact)" in out
    assert "provided at: api app.py" in out and "used at: web src/web/client.py" in out


def test_impact_json_is_stable_and_separates_unknown(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    edit_file(ws.roots["web"], "src/web/client.py", "get('/users')", "get(base + '/x')")
    code, out, _ = run(
        capsys,
        "workspace",
        "impact",
        "api",
        "--target",
        "route:GET /users",
        "--json",
        "--workspace",
        ws.file,
    )
    data = json.loads(out)
    assert code == 0 and data["dependents"] == []
    assert [d["label"] for d in data["may_depend"]] == ["computed_url"]


@pytest.mark.parametrize(
    ("target", "fragment"),
    [("route:GET /nope", "not found"), ("bogus", "not valid")],
)
def test_impact_exit_23_for_a_bad_or_missing_target(
    ws: Workspace, capsys: pytest.CaptureFixture[str], target: str, fragment: str
) -> None:
    code, out, err = run(
        capsys, "workspace", "impact", "api", "--target", target, "--workspace", ws.file
    )
    assert (code, out) == (23, "") and fragment in err and "/nope" not in err


def test_unknown_project_and_missing_workspace_exit_19(
    ws: Workspace, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, _, err = run(
        capsys, "workspace", "impact", "ghost", "--target", "package", "--workspace", ws.file
    )
    assert code == 19 and "no project has that name" in err
    code, _, _ = run(
        capsys, "workspace", "contract-diff", "api", "--workspace", tmp_path / "missing.sqlite3"
    )
    assert code == 19
    assert not (tmp_path / "missing.sqlite3").exists()  # read commands never create a file


def test_contract_diff_text_groups_verdicts_apart(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    edit_file(ws.roots["api"], "app.py", '"/orders"', '"/purchases"')
    edit_file(ws.roots["api"], "db/001.sql", "varchar(100)", "varchar(200)")
    code, out, err = run(capsys, "workspace", "contract-diff", "api", "--workspace", ws.file)
    assert code == 0, err
    assert "contract diff for api against HEAD" in out
    breaking = out.index("BREAKING")
    unknown = out.index("UNKNOWN")
    compatible = out.index("COMPATIBLE")
    assert breaking < unknown < compatible
    assert "POST /orders: route_removed_or_changed" in out[breaking:unknown]
    assert "type_detail_changed" in out[unknown:compatible]
    assert "route_added" in out[compatible:]


def test_contract_diff_json_and_a_fresh_process_agree(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    edit_file(ws.roots["api"], "app.py", '"/orders"', '"/purchases"')
    code, out, _ = run(
        capsys, "workspace", "contract-diff", "api", "--json", "--workspace", ws.file
    )
    assert code == 0
    first = json.loads(out)
    assert first["counts"]["breaking"] == 1 and len(first["base_commit"]) == 40
    again = subprocess.run(  # noqa: S603 - fixed argv in a test
        [
            sys.executable,
            "-m",
            "cognitive_mesh",
            "workspace",
            "contract-diff",
            "api",
            "--json",
            "--workspace",
            str(ws.file),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(again.stdout) == first


def test_base_ref_compares_with_an_earlier_commit(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    edit_file(ws.roots["api"], "app.py", '"/orders"', '"/purchases"')
    commit_all(ws.roots["api"], "rename")
    code, out, _ = run(
        capsys, "workspace", "contract-diff", "api", "--json", "--workspace", ws.file
    )
    assert code == 0 and json.loads(out)["findings"] == []  # HEAD equals the tree
    code, out, _ = run(
        capsys,
        "workspace",
        "contract-diff",
        "api",
        "--base",
        "HEAD~1",
        "--json",
        "--workspace",
        ws.file,
    )
    assert code == 0 and json.loads(out)["counts"]["breaking"] == 1


@pytest.mark.parametrize("ref", ["--output=x", "-h", "no-such-ref", "HEAD;rm", "a b", ""])
def test_a_bad_base_ref_exits_23_and_is_not_echoed(
    ws: Workspace, capsys: pytest.CaptureFixture[str], ref: str
) -> None:
    code, out, err = run(
        capsys, "workspace", "contract-diff", "api", f"--base={ref}", "--workspace", ws.file
    )
    assert (code, out) == (23, "")
    assert ref == "" or ref not in err


def test_the_repository_is_untouched_by_a_diff(
    ws: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    before = git(ws.roots["api"], "status", "--porcelain=v1", "--ignored")
    refs = git(ws.roots["api"], "for-each-ref")
    run(capsys, "workspace", "contract-diff", "api", "--workspace", ws.file)
    assert git(ws.roots["api"], "status", "--porcelain=v1", "--ignored") == before
    assert git(ws.roots["api"], "for-each-ref") == refs
    assert git(ws.roots["api"], "worktree", "list").count("\n") == 0


def test_a_project_outside_git_exits_23(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, git_projects=())
    code, _, err = run(capsys, "workspace", "contract-diff", "api", "--workspace", ws.file)
    assert code == 23 and "git repository" in err


def test_a_folder_inside_a_larger_repository_is_diffed_by_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cognitive_mesh.workspace import ProjectKind, WorkspaceStore
    from contracts_support import write_project
    from impact_support import API, WEB

    repo = tmp_path / "mono"
    write_project(repo / "services" / "api", API)
    write_project(tmp_path / "web", WEB)
    git(repo, "init", "-q")
    commit_all(repo)
    path = tmp_path / "w.sqlite3"
    with WorkspaceStore.create(path) as store:
        store.add_project("api", ProjectKind.LOCAL, str(repo / "services" / "api"))
        store.add_project("web", ProjectKind.LOCAL, str(tmp_path / "web"))
    edit_file(repo / "services" / "api", "app.py", '"/orders"', '"/purchases"')
    code, out, err = run(capsys, "workspace", "contract-diff", "api", "--json", "--workspace", path)
    assert code == 0, err
    assert json.loads(out)["counts"]["breaking"] == 1


# --- run --workspace ---------------------------------------------------------------------------

PLAN = {
    "task_id": "impact",
    "steps": [{"step_id": "d", "capability": "dependents_of", "inputs": {"target": "package"}}],
}


def test_run_with_workspace_gives_agents_the_other_projects(
    ws: Workspace, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    code, out, err = run(
        capsys,
        "run",
        plan,
        "--root",
        ws.roots["api"],
        "--workspace",
        ws.file,
        "--project",
        "api",
        "--json",
    )
    assert code == 0, err
    step = json.loads(out)["steps"][0]
    assert step["status"] == "succeeded"
    output = step["attempts"][0]["output"]
    assert {d["project"] for d in output["dependents"]} == {"web"}


def test_run_without_workspace_fails_the_step_explicitly(
    ws: Workspace, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    code, out, _ = run(capsys, "run", plan, "--root", ws.roots["api"], "--json")
    assert code != 0 and "missing_context" in out


def test_workspace_flags_must_come_together_and_match_the_root(
    ws: Workspace, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    code, _, err = run(capsys, "run", plan, "--root", ws.roots["api"], "--workspace", ws.file)
    assert code == 2 and "go together" in err
    code, _, err = run(
        capsys, "run", plan, "--root", ws.roots["web"], "--workspace", ws.file, "--project", "api"
    )
    assert code == 19 and "not the --root folder" in err
    code, _, err = run(
        capsys, "run", plan, "--root", ws.roots["api"], "--workspace", ws.file, "--project", "ghost"
    )
    assert code == 19 and "no project has that name" in err


def test_task_with_a_plan_takes_the_workspace_flags_and_checks_them_first(
    ws: Workspace, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    base: list[str | Path] = [
        "task", "impact", "--plan", plan, "--root", ws.roots["api"], "--db", tmp_path / "t.sqlite3",
        "--index", tmp_path / "idx", "--graph", tmp_path / "g.sqlite3",
    ]  # fmt: skip
    code, _, err = run(capsys, *base, "--workspace", ws.file)
    assert code == 2 and "go together" in err
    code, _, err = run(
        capsys, *base[:5], ws.roots["web"], *base[6:], "--workspace", ws.file, "--project", "api"
    )
    assert code == 19 and "not the --root folder" in err
    assert not (tmp_path / "t.sqlite3").exists()  # nothing was started
