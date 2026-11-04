"""Contract diff (ADR-0041): breaking, compatible, unknown and unused, from a ref to the tree."""

from pathlib import Path

from cognitive_mesh.impact import ContractDiff, Finding, Verdict, contract_diff
from impact_support import edit_file, make_workspace


def _diff(tmp: Path, edits: list[tuple[str, str, str]]) -> ContractDiff:
    ws = make_workspace(tmp)
    for rel, old, new in edits:
        edit_file(ws.roots["api"], rel, old, new)
    graph, reads = ws.load()
    return contract_diff("api", "HEAD", graph, reads)


def _pick(diff: ContractDiff, kind: str, reason: str, subject: str | None = None) -> list[Finding]:
    return [
        f
        for f in diff.findings
        if f.kind == kind and f.reason == reason and (subject is None or f.subject == subject)
    ]


def test_no_change_has_no_findings(tmp_path: Path) -> None:
    assert _diff(tmp_path, []).findings == ()


def test_renaming_a_route_a_dependent_calls_is_breaking(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [("app.py", '"/orders"', '"/purchases"')])
    (found,) = _pick(diff, "api", "route_removed_or_changed", "POST /orders")
    assert found.verdict is Verdict.BREAKING
    assert [d.project for d in found.dependents] == ["web"]
    assert found.dependents[0].site.path == "src/web/client.py"
    assert found.provider is not None and found.provider.path == "app.py"
    assert _pick(diff, "api", "route_added", "POST /purchases")[0].verdict is Verdict.COMPATIBLE


def test_a_param_style_change_that_still_matches_is_compatible_with_evidence(
    tmp_path: Path,
) -> None:
    diff = _diff(tmp_path, [("app.py", '"/users/{user_id}"', '"/users/{uid}"')])
    (found,) = _pick(diff, "api", "calls_still_match", "GET /users/{user_id}")
    assert found.verdict is Verdict.COMPATIBLE
    assert found.dependents and "GET /users/{uid}" in found.note


def test_removed_route_nobody_calls_is_unused(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit_file(ws.roots["api"], "app.py", '@app.get("/users")', '@app.get("/people")')
    edit_file(ws.roots["web"], "src/web/client.py", "get('/users')", "get('/people')")
    # web now calls /people, which api did not serve at the base: so /users is unused
    graph, reads = ws.load()
    diff = contract_diff("api", "HEAD", graph, reads)
    (found,) = _pick(diff, "api", "route_removed_or_changed", "GET /users")
    assert found.verdict is Verdict.UNUSED


def test_a_computed_caller_makes_a_removed_route_unknown(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    edit_file(ws.roots["web"], "src/web/client.py", "get('/users')", "get(base + '/x')")
    edit_file(ws.roots["api"], "app.py", '@app.get("/users")\ndef users(): ...\n', "")
    graph, reads = ws.load()
    diff = contract_diff("api", "HEAD", graph, reads)
    (found,) = _pick(diff, "api", "computed_callers", "GET /users")
    assert found.verdict is Verdict.UNKNOWN
    assert found.dependents


def test_removed_env_var_another_project_reads_is_breaking(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [(".env.example", "API_URL=\n", "")])
    (found,) = _pick(diff, "config", "setting_removed", "API_URL")
    assert found.verdict is Verdict.BREAKING
    assert found.dependents[0].project == "web"
    assert "no other project sets it" in found.note
    unused = _pick(
        _diff(tmp_path / "again", [(".env.example", "REGION=\n", "")]), "config", "setting_removed"
    )
    assert unused[0].verdict is Verdict.UNUSED


def test_column_type_change_in_a_shared_table_is_breaking(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [("db/001.sql", "email varchar(100)", "email integer")])
    (found,) = _pick(diff, "table", "type_changed", "table users.email")
    assert found.verdict is Verdict.BREAKING
    assert "varchar(100) -> integer" in found.note


def test_length_only_type_change_is_unknown(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [("db/001.sql", "varchar(100)", "varchar(200)")])
    (found,) = _pick(diff, "table", "type_detail_changed")
    assert found.verdict is Verdict.UNKNOWN


def test_removed_column_a_dependent_models_is_breaking(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [("db/001.sql", "    email varchar(100) NOT NULL,\n", "")])
    (found,) = _pick(diff, "table", "column_removed_or_renamed", "table users.email")
    assert found.verdict is Verdict.BREAKING
    unused = _pick(diff, "table", "column_removed_or_renamed", "table users.name")
    assert unused == []


def test_added_columns_nullable_is_compatible_required_is_unknown(tmp_path: Path) -> None:
    nullable = _diff(
        tmp_path / "a", [("db/001.sql", "    name text\n", "    name text,\n    bio text\n")]
    )
    assert (
        _pick(nullable, "table", "column_added", "table users.bio")[0].verdict is Verdict.COMPATIBLE
    )
    required = _diff(
        tmp_path / "b",
        [("db/001.sql", "    name text\n", "    name text,\n    bio text NOT NULL\n")],
    )
    assert _pick(required, "table", "required_column_added")[0].verdict is Verdict.UNKNOWN


def test_dropping_the_table_is_breaking(tmp_path: Path) -> None:
    diff = _diff(tmp_path, [("db/001.sql", "CREATE TABLE users", "CREATE TABLE accounts")])
    assert _pick(diff, "table", "table_removed", "table users")[0].verdict is Verdict.BREAKING


def test_version_outside_the_declared_range_is_breaking_inside_is_compatible(
    tmp_path: Path,
) -> None:
    out = _diff(tmp_path / "a", [("pyproject.toml", 'version = "1.2.0"', 'version = "2.0.0"')])
    (found,) = _pick(out, "package", "version_outside_declared_range")
    assert found.verdict is Verdict.BREAKING and "1.2.0 -> 2.0.0" in found.note
    inside = _diff(tmp_path / "b", [("pyproject.toml", 'version = "1.2.0"', 'version = "1.9.0"')])
    assert (
        _pick(inside, "package", "version_within_declared_range")[0].verdict is Verdict.COMPATIBLE
    )


def test_removed_export_a_dependent_imports_is_breaking(tmp_path: Path) -> None:
    diff = _diff(
        tmp_path,
        [("src/apilib/__init__.py", "['greet', 'shout']", "['shout']")],
    )
    (found,) = _pick(diff, "export", "export_removed", "export greet of apilib")
    assert found.verdict is Verdict.BREAKING
    unused = _pick(diff, "export", "export_removed")
    assert len(unused) == 1  # `shout` is still exported


def test_star_import_makes_the_export_list_unknown(tmp_path: Path) -> None:
    diff = _diff(
        tmp_path,
        [("src/apilib/__init__.py", "from .core import greet, shout\n", "from .core import *\n")],
    )
    assert _pick(diff, "export", "exports_incomplete")[0].verdict is Verdict.UNKNOWN
