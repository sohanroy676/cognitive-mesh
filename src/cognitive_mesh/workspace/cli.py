"""The `workspace` commands (ADR-0036). Read commands never create a file."""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cognitive_mesh.identity import derive_identity
from cognitive_mesh.impact import ImpactError, contract_diff, list_dependents, parse_target
from cognitive_mesh.impact.render import diff_json, diff_text, impact_json, impact_text
from cognitive_mesh.workspace.access import WorkspaceAccess
from cognitive_mesh.workspace.fetch import (
    checkout_dir,
    fetch_github,
    looks_like_url,
    normalise_github_url,
    parse_github_url,
)
from cognitive_mesh.workspace.graph import (
    ProjectRead,
    WorkspaceGraph,
    build_workspace_graph,
    link_reads,
    read_projects,
)
from cognitive_mesh.workspace.models import Project, ProjectKind, WorkspaceError
from cognitive_mesh.workspace.render import (
    deps_json,
    deps_text,
    to_json,
    to_mermaid,
    to_text,
)
from cognitive_mesh.workspace.store import DEFAULT_CHECK_TIMEOUT_S, WorkspaceStore

EXIT_OK = 0
EXIT_WORKSPACE_ERROR = 19
EXIT_IMPACT_ERROR = 23  # a target, ref or project the impact analysis cannot use (ADR-0041)


def add_parser(sub: Any) -> None:
    ws = sub.add_parser("workspace", help="projects that belong together and how they depend")
    ws_sub = ws.add_subparsers(dest="workspace_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", type=Path, required=True, help="the workspace file")

    def cached(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--cache", type=Path, default=None, help="clone cache (default: beside file)"
        )

    add = ws_sub.add_parser("add", help="add a local folder or a public GitHub repository")
    common(add)
    cached(add)
    add.add_argument("source", help="a folder, or https://github.com/OWNER/REPO")
    add.add_argument("--name", default=None, help="project name (default: derived)")
    listing = ws_sub.add_parser("list", help="list the projects")
    common(listing)
    listing.add_argument("--json", action="store_true", dest="as_json")
    remove = ws_sub.add_parser("remove", help="remove a project (its history stays)")
    common(remove)
    remove.add_argument("name")
    link = ws_sub.add_parser("link", help="declare an edge the analysis cannot see")
    common(link)
    link.add_argument("source")
    link.add_argument("target")
    link.add_argument("--kind", required=True)
    link.add_argument("--note", required=True)
    link.add_argument("--by", required=True)
    graph = ws_sub.add_parser("graph", help="show how the projects depend on each other")
    common(graph)
    cached(graph)
    graph.add_argument("--json", action="store_true", dest="as_json")
    graph.add_argument("--format", choices=["mermaid"], default=None, dest="fmt")
    deps = ws_sub.add_parser("deps", help="what one project uses and what uses it")
    common(deps)
    cached(deps)
    deps.add_argument("project")
    deps.add_argument("--json", action="store_true", dest="as_json")
    impact = ws_sub.add_parser("impact", help="which other projects depend on a target here")
    common(impact)
    cached(impact)
    impact.add_argument("project", help="the current project")
    impact.add_argument(
        "--target",
        required=True,
        help="file:PATH, route:METHOD /path, table:NAME, env:NAME, package or symbol:NAME",
    )
    impact.add_argument("--json", action="store_true", dest="as_json")
    diff = ws_sub.add_parser(
        "contract-diff", help="what a change to the current project does to the projects using it"
    )
    common(diff)
    cached(diff)
    diff.add_argument("project", help="the current project (a local folder in a git repository)")
    diff.add_argument("--base", default="HEAD", help="git ref to compare the working tree with")
    diff.add_argument("--json", action="store_true", dest="as_json")
    setc = ws_sub.add_parser(
        "set-check", help="declare the command that checks a project (never read from the repo)"
    )
    common(setc)
    setc.add_argument("project")
    setc.add_argument("--by", required=True, help="who declares it (an unauthenticated label)")
    setc.add_argument("--timeout", type=int, default=DEFAULT_CHECK_TIMEOUT_S, dest="timeout_s")
    setc.add_argument("check_argv", nargs=argparse.REMAINDER, help="-- COMMAND ARG... (no shell)")
    showc = ws_sub.add_parser("show-check", help="print the declared check command")
    common(showc)
    showc.add_argument("project")
    showc.add_argument("--json", action="store_true", dest="as_json")
    refresh = ws_sub.add_parser("refresh", help="fetch GitHub projects missing from the cache")
    common(refresh)
    cached(refresh)


def _cache(args: argparse.Namespace) -> Path:
    cache: Path | None = args.cache
    workspace: Path = args.workspace
    return cache if cache is not None else workspace.parent / "workspace-cache"


def _resolver(cache: Path) -> Callable[[Project], Path | None]:
    def resolve(project: Project) -> Path | None:
        if project.kind is ProjectKind.LOCAL:
            path = Path(project.location)
            return path if path.is_dir() else None
        assert project.commit is not None  # noqa: S101 - the store guarantees it
        path = checkout_dir(cache, project.location, project.commit)
        return path if path.is_dir() else None

    return resolve


def load_workspace_graph(workspace: Path, cache: Path | None = None) -> WorkspaceGraph:
    """The workspace graph of a workspace file (read-only); raises `WorkspaceError`."""
    with WorkspaceStore.open_existing(workspace) as store:
        projects = store.projects()
        links = store.links()
    resolver = _resolver(cache if cache is not None else workspace.parent / "workspace-cache")
    return build_workspace_graph(projects, links, resolver)


def load_workspace(
    workspace: Path, cache: Path | None = None
) -> tuple[WorkspaceGraph, dict[str, ProjectRead]]:
    """The workspace graph and each project's read (read-only); raises `WorkspaceError`."""
    with WorkspaceStore.open_existing(workspace) as store:
        projects = store.projects()
        links = store.links()
    resolver = _resolver(cache if cache is not None else workspace.parent / "workspace-cache")
    reads, diagnostics = read_projects(projects, resolver)
    return link_reads(projects, links, reads, diagnostics), reads


def build_access(workspace: Path, project: str, root: Path) -> WorkspaceAccess:
    """The workspace as agents may read it: other projects read-only, `project` the current one.
    `root` (the folder a run analyses or changes) must be that project's folder."""
    graph, reads = load_workspace(workspace)
    if project not in {p.name for p in graph.projects}:
        raise WorkspaceError("no project has that name")
    read = reads.get(project)
    if read is None:
        raise WorkspaceError("the project is not on disk; run `workspace refresh`")
    try:
        same = read.root.samefile(root)
    except OSError:
        same = False
    if not same:
        raise WorkspaceError("the project is not the --root folder")
    return WorkspaceAccess(project, graph, reads)


def run(args: argparse.Namespace) -> int:
    try:
        return _dispatch(args)
    except WorkspaceError as exc:
        print(f"workspace: {exc}", file=sys.stderr)
        return EXIT_WORKSPACE_ERROR
    except ImpactError as exc:
        print(f"workspace: {exc}", file=sys.stderr)
        return EXIT_IMPACT_ERROR


def _dispatch(args: argparse.Namespace) -> int:
    command = args.workspace_command
    if command == "add":
        return _add(args)
    if command == "link":
        with WorkspaceStore.create(args.workspace) as store:
            link = store.add_link(args.source, args.target, args.kind, args.note, args.by)
        print(f"declared link #{link.id}: {link.source} -> {link.target} (not detected evidence)")
        return EXIT_OK
    if command == "set-check":
        words = list(args.check_argv)
        if words[:1] == ["--"]:
            words = words[1:]
        with WorkspaceStore.create(args.workspace) as store:
            declared = store.set_check(args.project, words, timeout_s=args.timeout_s, by=args.by)
        print(
            f"declared check #{declared.id} for {declared.project}"
            f" (timeout {declared.timeout_s} s): {' '.join(declared.argv)}"
        )
        return EXIT_OK
    if command == "show-check":
        with WorkspaceStore.open_existing(args.workspace) as store:
            shown = store.check_command(args.project)
        if args.as_json:
            print(json.dumps(shown.model_dump(mode="json") if shown else None, indent=2))
        elif shown is None:
            print(f"no check command is declared for {args.project}")
        else:
            print(f"{' '.join(shown.argv)}  (timeout {shown.timeout_s} s, declared by {shown.by})")
        return EXIT_OK
    if command == "remove":
        with WorkspaceStore.create(args.workspace) as store:
            store.remove_project(args.name)
        print(f"removed {args.name} (its history is kept)")
        return EXIT_OK
    if command in ("impact", "contract-diff"):
        return _impact(args)
    with WorkspaceStore.open_existing(args.workspace) as store:
        projects = store.projects()
        if command == "list":
            return _list(args, projects)
        if command == "refresh":
            return _refresh(args, projects)
        wg = build_workspace_graph(projects, store.links(), _resolver(_cache(args)))
    if command == "deps":
        return _deps(args, wg)
    if args.as_json:
        print(json.dumps(to_json(wg), indent=2, sort_keys=True))
    elif args.fmt == "mermaid":
        print(to_mermaid(wg), end="")
    else:
        print(to_text(wg), end="")
    return EXIT_OK


def _impact(args: argparse.Namespace) -> int:
    wg, reads = load_workspace(args.workspace, args.cache)
    if args.project not in {p.name for p in wg.projects}:
        raise WorkspaceError("no project has that name")
    if args.project not in reads:
        raise WorkspaceError("the project is not on disk; run `workspace refresh`")
    if args.workspace_command == "impact":
        report = list_dependents(args.project, parse_target(args.target), wg, reads)
        if args.as_json:
            print(json.dumps(impact_json(report), indent=2, sort_keys=True))
        else:
            print(impact_text(report), end="")
        return EXIT_OK
    diff = contract_diff(args.project, args.base, wg, reads)
    if args.as_json:
        print(json.dumps(diff_json(diff), indent=2, sort_keys=True))
    else:
        print(diff_text(diff), end="")
    return EXIT_OK


def _deps(args: argparse.Namespace, wg: WorkspaceGraph) -> int:
    if args.project not in {p.name for p in wg.projects}:
        raise WorkspaceError("no project has that name")
    if args.as_json:
        print(json.dumps(deps_json(wg, args.project), indent=2, sort_keys=True))
    else:
        print(deps_text(wg, args.project), end="")
    return EXIT_OK


def _add(args: argparse.Namespace) -> int:
    source: str = args.source
    if looks_like_url(source):
        url = normalise_github_url(source)
        owner, repo = parse_github_url(url)
        directory, commit = fetch_github(url, _cache(args))
        identity = derive_identity(str(directory), declared=f"{owner}/{repo}")
        kind, location, name = ProjectKind.GITHUB, url, args.name or repo
    else:
        path = Path(source).resolve()
        if not path.is_dir():
            raise WorkspaceError("the folder does not exist")
        identity = derive_identity(str(path))
        kind, location, commit = ProjectKind.LOCAL, str(path), None
        name = args.name or (identity.app_id if identity else path.name)
    with WorkspaceStore.create(args.workspace) as store:
        project = store.add_project(
            name,
            kind,
            location,
            commit=commit,
            app_id=identity.app_id if identity else None,
            app_id_how=identity.how.value if identity else None,
        )
    pin = f" at {project.commit}" if project.commit else ""
    print(f"added {project.name} [{project.kind.value}]{pin}")
    return EXIT_OK


def _list(args: argparse.Namespace, projects: list[Project]) -> int:
    if args.as_json:
        rows = [p.model_dump(mode="json") for p in projects]
        print(json.dumps(rows, indent=2, sort_keys=True))
        return EXIT_OK
    if not projects:
        print("no projects in the workspace")
    for p in projects:
        pin = f" @ {p.commit}" if p.commit else ""
        print(f"{p.name} [{p.kind.value}] {p.location}{pin}")
    return EXIT_OK


def _refresh(args: argparse.Namespace, projects: list[Project]) -> int:
    cache = _cache(args)
    for p in projects:
        if p.kind is not ProjectKind.GITHUB:
            continue
        directory, _ = fetch_github(p.location, cache, p.commit)
        print(f"{p.name}: ready at {directory}")
    return EXIT_OK
