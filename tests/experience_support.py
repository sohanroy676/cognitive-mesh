"""Helpers for the experience graph tests: schema-valid agent outputs and small trace databases."""

from pathlib import Path

from pydantic import JsonValue

from cognitive_mesh.agents import AgentResult, Basis
from memory_support import add_run, failure, success

Step = tuple[str, str, dict[str, JsonValue], AgentResult]


def deps_output(
    node_id: str, direct: list[str], indirect: list[str] | None = None
) -> dict[str, JsonValue]:
    items: list[JsonValue] = [
        {"node_id": d, "node_kind": "module", "edge_kind": "imports", "depth": 1, "evidence": []}
        for d in direct
    ]
    items += [
        {"node_id": d, "node_kind": "module", "edge_kind": "imports", "depth": 2, "evidence": []}
        for d in indirect or []
    ]
    return {
        "node_id": node_id,
        "transitive": bool(indirect),
        "total": len(items),
        "truncated": False,
        "dependencies": items,
    }


def unresolved_output(node_ids: list[str]) -> dict[str, JsonValue]:
    entries: list[JsonValue] = [{"node_id": n, "candidate_package": None} for n in node_ids]
    return {
        "total": len(entries),
        "truncated": False,
        "unresolved": entries,
        "diagnostics_by_code": {},
    }


def deps_step(
    run_id: str,
    step_id: str,
    node_id: str,
    direct: list[str],
    indirect: list[str] | None = None,
) -> Step:
    result = success(
        run_id,
        step_id,
        "dependencies_of",
        deps_output(node_id, direct, indirect),
        Basis.DETERMINISTIC,
    )
    return (step_id, "dependencies_of", {"node_id": node_id}, result)


def unresolved_step(run_id: str, step_id: str, node_ids: list[str]) -> Step:
    result = success(run_id, step_id, "unresolved_report", unresolved_output(node_ids))
    return (step_id, "unresolved_report", {}, result)


def model_step(run_id: str, step_id: str) -> Step:
    result = success(
        run_id,
        step_id,
        "explain_file",
        {"summary": "a model wrote this"},
        Basis.MODEL_UNVERIFIED,
    )
    return (step_id, "explain_file", {"path": "a.py"}, result)


def failed_step(run_id: str, step_id: str) -> Step:
    return (
        step_id,
        "dependencies_of",
        {"node_id": "x"},
        failure(run_id, step_id, "dependencies_of", "down"),
    )


def make_db(tmp: Path, runs: dict[str, tuple[list[Step], str | None]]) -> Path:
    """One trace database. `runs` maps run id to (steps, source fingerprint)."""
    db = tmp / "traces.sqlite3"
    for run_id, (steps, fingerprint) in runs.items():
        add_run(db, run_id, steps, fingerprint=fingerprint)
    return db
