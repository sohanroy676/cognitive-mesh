"""Who depends on a target (ADR-0041): evidence on both sides, unknown kept, access read-only."""

import json
from pathlib import Path

import pytest

from cognitive_mesh.agents import AgentContext, AgentFailure, AgentSuccess, AgentTask, FailureKind
from cognitive_mesh.agents.reader import Reader
from cognitive_mesh.agents.registry import default_registry
from cognitive_mesh.impact import ImpactError, ImpactReport, list_dependents, parse_target
from cognitive_mesh.providers import ModelRequest, ModelResponse
from cognitive_mesh.workspace.access import ReadOnlyProjectError, WorkspaceAccess
from cognitive_mesh.workspace.models import WorkspaceError
from impact_support import edit_file, git, make_workspace


def _report(tmp: Path, target: str) -> ImpactReport:
    ws = make_workspace(tmp)
    graph, reads = ws.load()
    return list_dependents("api", parse_target(target), graph, reads)


def test_route_dependents_name_both_sites(tmp_path: Path) -> None:
    report = _report(tmp_path, "route:get /users")
    (found,) = [d for d in report.dependents if d.status == "detected"]
    assert (found.project, found.kind, found.subject, found.label) == (
        "web",
        "api",
        "GET /users",
        "exact",
    )
    assert found.provider is not None and found.provider.path == "app.py"
    assert found.consumers[0].path == "src/web/client.py"


def test_a_param_route_is_matched_with_its_label(tmp_path: Path) -> None:
    report = _report(tmp_path, "route:GET /users/{user_id}")
    assert [d.label for d in report.dependents] == ["value_for_param"]


def test_table_env_and_package_targets(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    graph, reads = ws.load()
    table = list_dependents("api", parse_target("table:USERS"), graph, reads)
    assert [(d.project, d.kind) for d in table.dependents] == [("web", "table")]
    env = list_dependents("api", parse_target("env:API_URL"), graph, reads)
    assert [(d.project, d.kind, d.label) for d in env.dependents] == [("web", "config", "reads")]
    pkg = list_dependents("api", parse_target("package"), graph, reads)
    kinds = {(d.kind, d.subject) for d in pkg.dependents}
    assert ("package", "apilib") in kinds and ("export", "greet of apilib") in kinds
    assert all(d.project == "web" for d in pkg.dependents)


def test_symbol_target_lists_importers_and_whole_module_users_as_unknown(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit_file(
        ws.roots["web"],
        "src/web/client.py",
        "from apilib import greet",
        "import apilib\nfrom apilib import greet",
    )
    graph, reads = ws.load()
    report = list_dependents("api", parse_target("symbol:greet"), graph, reads)
    assert {(d.status, d.label) for d in report.dependents} == {
        ("detected", "imported_by_name"),
        ("unknown", "module_imported_whole"),
    }


def test_file_target_expands_to_what_the_file_provides(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    graph, reads = ws.load()
    routes = list_dependents("api", parse_target("file:app.py"), graph, reads)
    assert {d.subject for d in routes.dependents if d.kind == "api"} == {
        "GET /users",
        "GET /users/{user_id}",
        "POST /orders",
    }
    init = list_dependents("api", parse_target("file:./src/apilib/__init__.py"), graph, reads)
    assert any(d.kind == "export" for d in init.dependents)


def test_computed_callers_are_listed_as_unknown_not_dropped(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit_file(ws.roots["web"], "src/web/client.py", "get('/users')", "get(base + '/x')")
    graph, reads = ws.load()
    report = list_dependents("api", parse_target("route:GET /users"), graph, reads)
    assert [(d.status, d.label) for d in report.dependents] == [("unknown", "computed_url")]


def test_declared_links_are_shown_apart(tmp_path: Path) -> None:
    from cognitive_mesh.workspace import WorkspaceStore

    ws = make_workspace(tmp_path)
    with WorkspaceStore.create(ws.file) as store:
        store.add_link("web", "api", "queue", "sends jobs", "me")
    graph, reads = ws.load()
    report = list_dependents("api", parse_target("route:GET /users"), graph, reads)
    assert [(link.kind, link.by) for link in report.declared] == [("queue", "me")]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "nope SECRET-MARKER",
        "route:SECRET-MARKER",
        "bogus:SECRET-MARKER",
        "table:",
        "file:\x00SECRET-MARKER",
        "x" * 400 + "SECRET-MARKER",
        "env",
    ],
)
def test_bad_target_forms_are_refused_without_echoing_them(text: str) -> None:
    with pytest.raises(ImpactError) as caught:
        parse_target(text)
    assert "SECRET-MARKER" not in str(caught.value)


@pytest.mark.parametrize(
    "target",
    ["route:GET /missing", "table:ghost", "env:NOPE", "file:nope.py", "symbol:ghost"],
)
def test_a_target_the_project_does_not_have_is_refused(tmp_path: Path, target: str) -> None:
    ws = make_workspace(tmp_path)
    graph, reads = ws.load()
    with pytest.raises(ImpactError, match="not found in the current project"):
        list_dependents("api", parse_target(target), graph, reads)


# --- access ------------------------------------------------------------------------------------


def test_only_the_current_project_may_be_written(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    graph, reads = ws.load()
    access = WorkspaceAccess("api", graph, reads)
    access.require_writable("api")
    with pytest.raises(ReadOnlyProjectError) as caught:
        access.require_writable("web")
    assert "web" not in str(caught.value)
    assert isinstance(caught.value, WorkspaceError)
    with pytest.raises(TypeError):
        access.reads["x"] = reads["web"]  # type: ignore[index]


def test_a_reader_gives_file_text_and_nothing_to_write_with(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    graph, reads = ws.load()
    reader = WorkspaceAccess("api", graph, reads).reader("web")
    assert "src/web/client.py" in reader.paths
    file = reader.file("src/web/client.py")
    assert file is not None and "requests.get" in file.content and len(file.sha256) == 64
    assert reader.file("../outside") is None
    assert not [n for n in dir(reader) if "write" in n or "open" in n or n.endswith("path")]
    with pytest.raises(WorkspaceError):
        WorkspaceAccess("api", graph, reads).reader("ghost")
    with pytest.raises(WorkspaceError):
        WorkspaceAccess("ghost", graph, reads)


# --- agents ------------------------------------------------------------------------------------


class _Cites:
    provider, model = "fake", "m"

    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.prompts.append(request.prompt)
        return ModelResponse(provider="fake", model="m", text=json.dumps(self.reply))


def _task(inputs: dict[str, object], capability: str = "summarise_dependents") -> AgentTask:
    return AgentTask(task_id="t", capability=capability, inputs=inputs)


def _context(tmp: Path) -> AgentContext:
    ws = make_workspace(tmp)
    graph, reads = ws.load()
    return AgentContext(workspace=WorkspaceAccess("api", graph, reads))


def test_reader_sends_only_dependent_files_as_data_and_marks_the_reply_unverified(
    tmp_path: Path,
) -> None:
    client = _Cites(
        {"summaries": [{"project": "web", "summary": "calls it", "paths": ["src/web/client.py"]}]}
    )
    result = Reader(client).run(_task({"target": "route:GET /users"}), _context(tmp_path))
    assert isinstance(result, AgentSuccess)
    assert result.basis.value == "model_unverified" and result.model is not None
    sent = json.loads(client.prompts[0])
    assert {(f["project"], f["path"]) for f in sent["files"]} == {("web", "src/web/client.py")}
    assert all(e.path.startswith("web:") and len(e.sha256) == 64 for e in result.evidence)


def test_reader_refuses_a_cited_path_it_was_not_given(tmp_path: Path) -> None:
    client = _Cites({"summaries": [{"project": "web", "summary": "x", "paths": ["secret.txt"]}]})
    result = Reader(client).run(_task({"target": "route:GET /users"}), _context(tmp_path))
    assert isinstance(result, AgentFailure) and result.kind is FailureKind.MALFORMED_OUTPUT
    assert "secret.txt" not in result.message


def test_reader_and_analyst_need_a_workspace_and_a_known_target(tmp_path: Path) -> None:
    registry = default_registry(_Cites({"summaries": []}))
    context = _context(tmp_path)
    for agent, cap in (("reader", "summarise_dependents"), ("dependency_analyst", "dependents_of")):
        bare = registry.get(agent).run(_task({"target": "package"}, cap), AgentContext())
        assert isinstance(bare, AgentFailure) and bare.kind is FailureKind.MISSING_CONTEXT
        bad = registry.get(agent).run(_task({"target": "route:GET /nope"}, cap), context)
        assert isinstance(bad, AgentFailure) and bad.kind is FailureKind.INVALID_INPUT
        assert "/nope" not in bad.message


def test_dependents_of_is_deterministic(tmp_path: Path) -> None:
    result = (
        default_registry()
        .get("dependency_analyst")
        .run(_task({"target": "route:GET /users"}, "dependents_of"), _context(tmp_path))
    )
    assert isinstance(result, AgentSuccess) and result.basis.value == "deterministic"
    assert result.output["total"] == 1


def test_other_projects_are_left_untouched(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, git_projects=("api", "web"))
    before = git(ws.roots["web"], "status", "--porcelain")
    graph, reads = ws.load()
    list_dependents("api", parse_target("package"), graph, reads)
    assert git(ws.roots["web"], "status", "--porcelain") == before == ""
