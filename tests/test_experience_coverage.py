"""Attempt coverage in the experience graph (ADR-0016): did a listing say it was complete?"""

from pathlib import Path

from cognitive_mesh.experience import NodeKind, Snapshot, build_from_traces
from cognitive_mesh.traces import TraceStore
from experience_support import deps_step, failed_step, make_db, model_step, unresolved_step

A, B = "module:a.py", "module:b.py"


def coverage_of(snap: Snapshot) -> dict[str, str | None]:
    return {n.id: n.coverage for n in snap.nodes if n.kind is NodeKind.ATTEMPT}


def build(db: Path) -> Snapshot:
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def test_complete_and_truncated_listings_are_marked(tmp_path: Path) -> None:
    steps = [
        deps_step("r1", "s1", A, [B]),
        deps_step("r1", "s2", A, [B], truncated=True),
        unresolved_step("r1", "s3", [B]),
        unresolved_step("r1", "s4", [B], truncated=True),
    ]
    snap = build(make_db(tmp_path, {"r1": (steps, "fp")}))
    assert coverage_of(snap) == {
        "attempt:r1/s1/1": "complete",
        "attempt:r1/s2/1": "truncated",
        "attempt:r1/s3/1": "complete",
        "attempt:r1/s4/1": "truncated",
    }


def test_model_and_failed_attempts_say_nothing_about_absence(tmp_path: Path) -> None:
    steps = [model_step("r1", "s1"), failed_step("r1", "s2")]
    snap = build(make_db(tmp_path, {"r1": (steps, "fp")}))
    assert set(coverage_of(snap).values()) == {None}
