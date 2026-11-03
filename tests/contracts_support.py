"""Helpers for the ADR-0037 tests: build small projects on disk and graph them."""

from datetime import UTC, datetime
from pathlib import Path

from cognitive_mesh.workspace import Project, ProjectKind, WorkspaceGraph, build_workspace_graph

Files = dict[str, str]


def write_project(root: Path, files: Files) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    root.mkdir(parents=True, exist_ok=True)
    return root


def graph_of(tmp: Path, projects: dict[str, Files]) -> WorkspaceGraph:
    rows = []
    for i, (name, files) in enumerate(projects.items(), 1):
        root = write_project(tmp / name, files)
        rows.append(
            Project(
                id=i,
                name=name,
                kind=ProjectKind.LOCAL,
                location=str(root),
                commit=None,
                app_id=None,
                app_id_how=None,
                added_at=datetime.now(UTC),
            )
        )
    return build_workspace_graph(rows, [], lambda p: Path(p.location))


def api_pairs(graph: WorkspaceGraph) -> set[tuple[str, str, str, str, str]]:
    return {(e.source, e.target, e.method, e.path, e.match) for e in graph.api}


def reasons(graph: WorkspaceGraph, kind: str) -> set[tuple[str, str]]:
    return {(u.project, u.reason) for u in graph.unresolved if u.kind == kind}


FASTAPI = """\
from fastapi import FastAPI, APIRouter

app = FastAPI()
router = APIRouter(prefix="/v1")
loose = APIRouter()


@app.get("/users")
def users(): ...


@app.get("/users/{user_id}")
def user(user_id: int): ...


@app.post("/orders")
def order(): ...


@router.get("/items")
def items(): ...


@loose.get("/hidden")
def hidden(): ...


app.include_router(router, prefix="/api")
"""
