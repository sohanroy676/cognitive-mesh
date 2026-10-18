"""Expiry, scope and environment in claim assessment and the gate (ADR-0022)."""

from datetime import UTC, date, datetime
from pathlib import Path

from cognitive_mesh.experience import ExperienceGraph, Snapshot, build_from_traces, check_sources
from cognitive_mesh.experience.assess import (
    ClaimAssessment,
    ClaimStatus,
    Corroboration,
    assess_claims,
)
from cognitive_mesh.experience.build import claim_id
from cognitive_mesh.experience.current import CurrentSource
from cognitive_mesh.experience.models import NodeKind
from cognitive_mesh.experience.reviews import Review, Verdict
from cognitive_mesh.graph import build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.retrieval import build_context, recall
from cognitive_mesh.retrieval.context import REASON_ENVIRONMENT_CHANGED, REASON_EXPIRED
from cognitive_mesh.traces import TraceStore
from cognitive_mesh.validity import Environment, EnvScope, ValidityState
from experience_support import deps_step, make_db
from test_recall import Scripted

A, B = "module:src/dep_app/a.py", "module:src/dep_app/b.py"
FP = "1" * 64
T0 = datetime(2026, 10, 1, tzinfo=UTC)
TODAY = date(2026, 10, 18)
CLAIM = claim_id("depends_on", A, B, "imports")
FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
LINUX = Environment(values={"platform": "Linux", "python_version": "3.12.3"})
WINDOWS = Environment(values={"platform": "Windows", "python_version": "3.12.3"})


def snapshot(tmp_path: Path) -> Snapshot:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP)})
    with TraceStore.open_existing(db) as store:
        return build_from_traces(store)


def review(
    n: int, verdict: Verdict = Verdict.CONFIRMED, until: date | None = None, day: int = 2
) -> Review:
    return Review(
        id=n,
        claim_id=CLAIM,
        fingerprint=FP,
        verdict=verdict,
        note="n",
        reviewer="r",
        created_at=datetime(2026, 10, day, tzinfo=UTC),
        valid_until=until,
    )


def assess(
    tmp_path: Path,
    reviews: list[Review],
    *,
    today: date | None = TODAY,
    scope: EnvScope | None = EnvScope.CODE,
    recorded: Environment | None = None,
    here: Environment | None = None,
) -> ClaimAssessment:
    snap = snapshot(tmp_path)
    nodes = tuple(
        n.model_copy(update={"scope": scope, "environment": recorded})
        if n.kind is NodeKind.CLAIM
        else n
        for n in snap.nodes
    )
    scoped = Snapshot(nodes=nodes, edges=snap.edges)
    graph = ExperienceGraph(scoped)
    with TraceStore.open_existing(tmp_path / "traces.sqlite3") as store:
        report = check_sources(scoped, store)
    current = CurrentSource(FP, build_dependency_graph(ingest_application(FIXTURE)))
    return assess_claims(
        graph, report, reviews, current, today=today, environment=here or Environment()
    )[CLAIM]


def test_a_claim_without_an_expiry_never_expires_by_age(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1)])
    assert got.status is ClaimStatus.VALIDATED
    assert got.validity is not None and got.validity.state is ValidityState.IN_FORCE
    assert got.valid_until is None


def test_a_passed_valid_until_expires_the_claim_and_it_is_not_dropped(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1, until=date(2026, 10, 17))])
    assert got.status is ClaimStatus.EXPIRED
    assert got.valid_until == date(2026, 10, 17) and got.validated_by == ()
    assert len(got.reviews) == 1 and len(got.supporting) == 1  # history kept


def test_the_last_day_and_the_future_are_still_in_force(tmp_path: Path) -> None:
    assert assess(tmp_path / "a", [review(1, until=TODAY)]).status is ClaimStatus.VALIDATED
    assert assess(tmp_path / "b", [review(1, until=date(2027, 1, 1))]).status is (
        ClaimStatus.VALIDATED
    )


def test_without_a_clock_an_expiry_is_unknown_not_expired(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1, until=date(2020, 1, 1))], today=None)
    assert got.status is ClaimStatus.VALIDATED
    assert got.validity is not None and got.validity.state is ValidityState.UNKNOWN


def test_a_newer_record_with_a_later_date_renews_an_older_expiry(tmp_path: Path) -> None:
    old = review(1, until=date(2026, 10, 10), day=2)
    renewed = review(2, until=date(2027, 1, 1), day=5)
    assert assess(tmp_path / "a", [old, renewed]).status is ClaimStatus.VALIDATED
    assert assess(tmp_path / "b", [old]).status is ClaimStatus.EXPIRED


def test_a_newer_record_without_a_date_does_not_cancel_an_older_expiry(tmp_path: Path) -> None:
    old = review(1, until=date(2026, 10, 10), day=2)
    newer = review(2, day=5)
    assert assess(tmp_path, [old, newer]).status is ClaimStatus.EXPIRED


def test_a_conflict_outranks_expiry(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1, until=date(2020, 1, 1)), review(2, Verdict.REJECTED)])
    assert got.status is ClaimStatus.CONFLICTING


def test_an_expired_claim_is_not_corroborated(tmp_path: Path) -> None:
    fresh = assess(tmp_path / "a", [review(1)])
    expired = assess(tmp_path / "b", [review(1, until=date(2020, 1, 1))])
    assert fresh.corroboration is Corroboration.CORROBORATED
    assert expired.corroboration is Corroboration.SINGLE
    assert "not corroborated" in expired.corroboration_reason


def test_a_code_claim_ignores_the_environment(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1)], recorded=WINDOWS, here=LINUX)
    assert got.status is ClaimStatus.VALIDATED


def test_an_environment_claim_follows_the_comparison(tmp_path: Path) -> None:
    kw = {"scope": EnvScope.ENVIRONMENT, "recorded": WINDOWS}
    same = assess(tmp_path / "a", [review(1)], here=WINDOWS, **kw)  # type: ignore[arg-type]
    changed = assess(tmp_path / "b", [review(1)], here=LINUX, **kw)  # type: ignore[arg-type]
    unknown = assess(tmp_path / "c", [review(1)], here=Environment(), **kw)  # type: ignore[arg-type]
    assert same.status is ClaimStatus.VALIDATED
    assert changed.status is ClaimStatus.ENVIRONMENT_CHANGED and changed.validated_by == ()
    assert unknown.status is ClaimStatus.ENVIRONMENT_UNVERIFIED
    assert changed.validity is not None and changed.validity.comparison is not None
    assert changed.validity.comparison.different == ("platform",)


def test_an_unscoped_claim_is_labelled_and_otherwise_unchanged(tmp_path: Path) -> None:
    got = assess(tmp_path, [review(1)], scope=None)
    assert got.status is ClaimStatus.VALIDATED
    assert got.validity is not None and got.validity.state is ValidityState.UNSCOPED


def test_built_claims_are_code_scoped_and_runs_record_the_environment(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    claims = [n for n in snap.nodes if n.kind is NodeKind.CLAIM]
    runs = [n for n in snap.nodes if n.kind is NodeKind.RUN]
    assert claims and all(n.scope is EnvScope.CODE for n in claims)
    (run,) = runs
    assert run.environment is not None and set(run.environment.values) == {
        "analyser_version",
        "python_version",
        "platform",
    }


def gate_reasons(tmp_path: Path, reviews: list[Review], here: Environment) -> list[str]:
    snap = snapshot(tmp_path)
    nodes = tuple(
        n.model_copy(update={"scope": EnvScope.ENVIRONMENT, "environment": WINDOWS})
        if n.kind is NodeKind.CLAIM
        else n
        for n in snap.nodes
    )
    snap = Snapshot(nodes=nodes, edges=snap.edges)
    with TraceStore.open_existing(tmp_path / "traces.sqlite3") as store:
        hits = Scripted([("r1", "s1", 1, 0.9)])
        found = recall(
            "q",
            hits,
            store,
            snap,
            reviews=reviews,
            k=5,
            min_similarity=0.2,
            today=TODAY,
            environment=here,
        )
        graph = build_dependency_graph(ingest_application(FIXTURE))
        got = build_context("q", found, ExperienceGraph(snap), graph, store, str(FIXTURE))
    assert got.pack.claims == ()
    return [n.reason for n in got.not_applicable]


def test_the_gate_withholds_expired_and_environment_changed_claims_with_a_reason(
    tmp_path: Path,
) -> None:
    expired = gate_reasons(tmp_path / "a", [review(1, until=date(2020, 1, 1))], WINDOWS)
    changed = gate_reasons(tmp_path / "b", [], LINUX)
    assert expired == [REASON_EXPIRED]
    assert changed == [REASON_ENVIRONMENT_CHANGED]


def test_scope_and_environment_survive_the_graph_file(tmp_path: Path) -> None:
    from cognitive_mesh.experience import store as graph_store

    path = tmp_path / "g.sqlite3"
    graph_store.save(path, snapshot(tmp_path / "t"))
    loaded = graph_store.load(path).snapshot
    claims = [n for n in loaded.nodes if n.kind is NodeKind.CLAIM]
    runs = [n for n in loaded.nodes if n.kind is NodeKind.RUN]
    assert all(n.scope is EnvScope.CODE for n in claims)
    assert all(n.environment is not None for n in runs)
