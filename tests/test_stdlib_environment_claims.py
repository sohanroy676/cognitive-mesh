"""Standard-library membership as the first real environment-dependent claim (ADR-0025)."""

from datetime import date
from pathlib import Path

import pytest

from cognitive_mesh.experience import ExperienceGraph, Snapshot, build_from_traces, check_sources
from cognitive_mesh.experience.assess import ClaimAssessment, ClaimStatus, assess_claims
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource
from cognitive_mesh.experience.models import NodeKind
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.retrieval import GatedContext, build_context, recall
from cognitive_mesh.retrieval.context import REASON_ENVIRONMENT_CHANGED
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.validity import (
    ENV_IDENTIFIERS,
    Environment,
    EnvScope,
    current_environment,
    python_minor,
)
from experience_support import deps_step, make_db
from test_recall import Scripted

A, B = "module:src/dep_app/a.py", "module:src/dep_app/b.py"
OS = "stdlib:os"
NODE_FS = "stdlib:node:fs"
FP = "1" * 64
TODAY = date(2026, 10, 18)
FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
PY312 = Environment(values={"python_minor": "3.12"})
PY310 = Environment(values={"python_minor": "3.10"})


def snapshot(
    tmp_path: Path, runs: dict[str, str], objects: tuple[str, ...] = (B, OS, NODE_FS)
) -> tuple[Snapshot, Path]:
    """One run per entry of `runs` (run id -> recorded python_version)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(
        tmp_path,
        {r: ([deps_step(r, "s1", A, list(objects))], FP) for r in runs},
        pythons=runs,
    )
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store), db


def claim(snap: Snapshot, obj: str):  # type: ignore[no-untyped-def]
    cid = claim_id("depends_on", A, obj, "imports")
    (node,) = [n for n in snap.nodes if n.id == cid and n.kind is NodeKind.CLAIM]
    return node


def assess(snap: Snapshot, db: Path, obj: str, here: Environment) -> ClaimAssessment:
    graph = ExperienceGraph(snap)
    with TraceStore.open_existing(db) as store:
        report = check_sources(snap, store)
    current = CurrentSource(FP, build_dependency_graph(ingest_application(FIXTURE)))
    return assess_claims(graph, report, [], current, today=TODAY, environment=here)[
        claim_id("depends_on", A, obj, "imports")
    ]


def test_python_minor_is_parsed_and_never_guessed() -> None:
    assert python_minor("3.12.3") == "3.12"
    assert python_minor("3.12") == "3.12"
    assert python_minor("3.9.18") == "3.9"
    for bad in ("", "three", "3", "x.12.1", "3.x", "3.12abc"):
        assert python_minor(bad) is None


def test_the_current_environment_names_the_minor() -> None:
    assert "python_minor" in ENV_IDENTIFIERS
    minor = current_environment().values.get("python_minor")
    assert minor is not None and python_minor(minor) == minor


def test_only_python_stdlib_edges_become_environment_claims(tmp_path: Path) -> None:
    snap, _ = snapshot(tmp_path, {"r1": "3.12.3"})
    assert claim(snap, OS).scope is EnvScope.ENVIRONMENT
    assert claim(snap, OS).environment == PY312
    assert claim(snap, B).scope is EnvScope.CODE and claim(snap, B).environment is None
    # Node built-ins depend on Node, which this claim type does not compare
    assert claim(snap, NODE_FS).scope is EnvScope.CODE


def test_unresolved_edges_stay_code_claims(tmp_path: Path) -> None:
    snap, _ = snapshot(tmp_path, {"r1": "3.12.3"}, ("unresolved:import:yaml",))
    assert claim(snap, "unresolved:import:yaml").scope is EnvScope.CODE


def test_same_minor_keeps_the_normal_status(tmp_path: Path) -> None:
    snap, db = snapshot(tmp_path, {"r1": "3.12.3"})
    got = assess(snap, db, OS, PY312)
    assert got.status in (ClaimStatus.SUPPORTED, ClaimStatus.VALIDATED)


def test_a_patch_release_difference_does_not_change_the_status(tmp_path: Path) -> None:
    snap, db = snapshot(tmp_path, {"r1": "3.12.1"})
    assert assess(snap, db, OS, PY312).status is not ClaimStatus.ENVIRONMENT_CHANGED


def test_a_different_minor_makes_the_claim_environment_changed(tmp_path: Path) -> None:
    snap, db = snapshot(tmp_path, {"r1": "3.12.3"})
    got = assess(snap, db, OS, PY310)
    assert got.status is ClaimStatus.ENVIRONMENT_CHANGED
    assert got.validated_by == ()


def test_a_first_party_claim_ignores_the_python_minor(tmp_path: Path) -> None:
    snap, db = snapshot(tmp_path, {"r1": "3.12.3"})
    assert assess(snap, db, B, PY310).status is not ClaimStatus.ENVIRONMENT_CHANGED


@pytest.mark.parametrize("recorded", ["", "unknown", "3"])
def test_an_unparsable_recorded_version_is_unverified_not_same(
    tmp_path: Path, recorded: str
) -> None:
    snap, db = snapshot(tmp_path, {"r1": recorded or "?"})
    assert claim(snap, OS).environment == Environment()
    assert assess(snap, db, OS, PY312).status is ClaimStatus.ENVIRONMENT_UNVERIFIED


def test_a_missing_current_value_is_unverified(tmp_path: Path) -> None:
    snap, db = snapshot(tmp_path, {"r1": "3.12.3"})
    assert assess(snap, db, OS, Environment()).status is ClaimStatus.ENVIRONMENT_UNVERIFIED


def test_one_claim_seen_on_two_minors_is_unverified_and_never_validated(tmp_path: Path) -> None:
    runs = {"r1": "3.12.3", "r2": "3.10.14"}
    snap, db = snapshot(tmp_path, runs)
    merged = claim(snap, OS)
    assert merged.environment == Environment()  # no single environment is recorded
    run_envs = {
        n.environment.values["python_version"]
        for n in snap.nodes
        if n.kind is NodeKind.RUN and n.environment is not None
    }
    assert len(run_envs) == 2  # the run nodes keep each environment
    for here in (PY312, PY310):
        got = assess(snap, db, OS, here)
        assert got.status is ClaimStatus.ENVIRONMENT_UNVERIFIED and got.validated_by == ()


def test_the_merge_does_not_depend_on_run_order(tmp_path: Path) -> None:
    a, _ = snapshot(tmp_path / "a", {"r1": "3.12.3", "r2": "3.10.14", "r3": "3.12.1"})
    b, _ = snapshot(tmp_path / "b", {"r3": "3.12.1", "r2": "3.10.14", "r1": "3.12.3"})
    assert claim(a, OS).environment == claim(b, OS).environment == Environment()


def test_two_runs_on_the_same_minor_keep_that_minor(tmp_path: Path) -> None:
    snap, _ = snapshot(tmp_path, {"r1": "3.12.3", "r2": "3.12.7"})
    assert claim(snap, OS).environment == PY312


def gated(tmp_path: Path, python: str, here: Environment) -> GatedContext:
    snap, db = snapshot(tmp_path, {"r1": python}, (OS,))
    with TraceStore.open_existing(db) as store:
        result = recall(
            "q",
            Scripted([("r1", "s1", 1, 0.9)]),
            store,
            snap,
            k=5,
            min_similarity=0.2,
            environment=here,
        )
        graph = build_dependency_graph(ingest_application(FIXTURE))
        return build_context("q", result, ExperienceGraph(snap), graph, store, "/work/dep_app")


def test_the_pack_withholds_a_changed_stdlib_claim_with_the_reason(tmp_path: Path) -> None:
    got = gated(tmp_path / "a", "3.12.3", PY310)
    assert got.pack.claims == () and got.pack.withheld.not_applicable == 1
    assert [n.reason for n in got.not_applicable] == [REASON_ENVIRONMENT_CHANGED]
    kept = gated(tmp_path / "b", "3.12.3", PY312)
    assert len(kept.pack.claims) == 1


def test_an_unverified_stdlib_claim_is_offered_with_its_label(tmp_path: Path) -> None:
    got = gated(tmp_path, "3.12.3", Environment())
    (item,) = got.pack.claims
    assert item.status == ClaimStatus.ENVIRONMENT_UNVERIFIED.value
