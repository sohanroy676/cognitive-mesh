"""The `workspace` commands (ADR-0036). Read commands never create a file."""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cognitive_mesh.identity import derive_identity
from cognitive_mesh.workspace.fetch import (
    checkout_dir,
    fetch_github,
    looks_like_url,
    normalise_github_url,
    parse_github_url,
)
from cognitive_mesh.workspace.graph import build_workspace_graph
from cognitive_mesh.workspace.models import Project, ProjectKind, WorkspaceError
from cognitive_mesh.workspace.render import to_json, to_mermaid, to_text
from cognitive_mesh.workspace.store import WorkspaceStore

EXIT_OK = 0
EXIT_WORKSPACE_ERROR = 19


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


def run(args: argparse.Namespace) -> int:
    try:
        return _dispatch(args)
    except WorkspaceError as exc:
        print(f"workspace: {exc}", file=sys.stderr)
        return EXIT_WORKSPACE_ERROR


def _dispatch(args: argparse.Namespace) -> int:
    command = args.workspace_command
    if command == "add":
        return _add(args)
    if command == "link":
        with WorkspaceStore.create(args.workspace) as store:
            link = store.add_link(args.source, args.target, args.kind, args.note, args.by)
        print(f"declared link #{link.id}: {link.source} -> {link.target} (not detected evidence)")
        return EXIT_OK
    if command == "remove":
        with WorkspaceStore.create(args.workspace) as store:
            store.remove_project(args.name)
        print(f"removed {args.name} (its history is kept)")
        return EXIT_OK
    with WorkspaceStore.open_existing(args.workspace) as store:
        projects = store.projects()
        if command == "list":
            return _list(args, projects)
        if command == "refresh":
            return _refresh(args, projects)
        wg = build_workspace_graph(projects, store.links(), _resolver(_cache(args)))
    if args.as_json:
        print(json.dumps(to_json(wg), indent=2, sort_keys=True))
    elif args.fmt == "mermaid":
        print(to_mermaid(wg), end="")
    else:
        print(to_text(wg), end="")
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
