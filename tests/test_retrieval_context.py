"""Building the agent-facing pack from a recall result (ADR-0017): gates, labels, no leaks."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cognitive_mesh.agents.context import MAX_CLAIMS, NO_PRIOR_CONTEXT, RAW_LABEL, RecalledContext
from cognitive_mesh.experience import Snapshot, build_from_traces
from cognitive_mesh.experience.assess import STATUS_MEANING, ClaimStatus
from cognitive_mesh.experience.graph import ExperienceGraph
from cognitive_mesh.graph import DependencyGraph, build_dependency_graph
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.memory.models import SourceCheck, UnverifiedHit
from cognitive_mesh.retrieval import GatedContext, build_context, recall, root_name
from cognitive_mesh.retrieval.context import REASON_SUBJECT
from cognitive_mesh.traces import TraceStore
from experience_support import Step, deps_step, failed_step, make_db, model_step
from test_recall import Scripted

FIXTURE = Path(__file__).parent / "fixtures" / "dep_app"
A = "module:src/dep_app/a.py"
B = "module:src/dep_app/b.py"
C = "module:src/dep_app/util.py"
GONE = "module:src/elsewhere/gone.py"
FP1 = "1" * 64
APP = "/work/dep_app"


def current_graph() -> DependencyGraph:
    return build_dependency_graph(ingest_application(FIXTURE))


def gated(
    tmp_path: Path,
    runs: Mapping[str, tuple[list[Step], str | None]],
    searcher: Scripted,
    roots: Mapping[str, str] | None = None,
    here: str = APP,
) -> GatedContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, runs, roots)
    with TraceStore.open_existing(db) as store:
        snap: Snapshot = build_from_traces(store)
        result = recall("q", searcher, store, snap, k=5, min_similarity=0.2)
        return build_context("q", result, ExperienceGraph(snap), current_graph(), store, here)


def pack(*args: Any, **kwargs: Any) -> RecalledContext:
    return gated(*args, **kwargs).pack


def test_a_relevant_claim_arrives_with_status_meaning_and_ids(tmp_path: Path) -> None:
    got = pack(
        tmp_path, {"r1": ([deps_step("r1", "s1", A, [B])], FP1)}, Scripted([("r1", "s1", 1, 0.8)])
    )
    (claim,) = got.claims
    assert not got.empty and got.message == ""
    assert (claim.subject, claim.object, claim.detail) == (A, B, "imports")
    assert claim.status == ClaimStatus.SUPPORTED.value
    assert claim.meaning == STATUS_MEANING[ClaimStatus.SUPPORTED]
    assert [(s.run_id, s.doc_id) for s in claim.supporting] == [("r1", "r1/s1/1")]
    assert claim.retrieved_via_doc_ids == ("r1/s1/1",) and claim.best_similarity == 0.8


def test_a_conflicting_claim_carries_both_sides(tmp_path: Path) -> None:
    runs = {
        "r1": ([deps_step("r1", "s1", A, [B])], FP1),
        "r2": ([deps_step("r2", "s1", A, [C])], FP1),
    }
    got = pack(tmp_path, runs, Scripted([("r2", "s1", 1, 0.9)]))
    (claim,) = got.claims
    assert claim.status == ClaimStatus.CONFLICTING.value
    assert [s.run_id for s in claim.supporting] == ["r2"]
    assert [(c.run_id, c.same_version) for c in claim.contradicting] == [("r1", True)]


def test_a_claim_about_a_subject_not_in_this_application_is_withheld(tmp_path: Path) -> None:
    runs = {"r1": ([deps_step("r1", "s1", GONE, [B])], FP1)}
    got = pack(tmp_path, runs, Scripted([("r1", "s1", 1, 0.99)]))
    assert got.claims == () and got.empty and got.message == NO_PRIOR_CONTEXT
    assert got.withheld.not_applicable == 1 and got.withheld.unconfirmed == 0


def test_raw_experience_is_labelled_and_gated_by_the_root_name(tmp_path: Path) -> None:
    runs = {"r1": ([model_step("r1", "s1"), failed_step("r1", "s2")], FP1)}
    hit = Scripted([("r1", "s2", 1, 0.9)], basis=None, status="failure")
    same = pack(tmp_path / "a", runs, hit, roots={"r1": "C:\\other\\place\\dep_app"})
    (raw,) = same.raw
    assert (raw.kind, raw.label, raw.result_status) == ("failed", RAW_LABEL, "failure")
    assert same.claims == () and not same.empty
    other = pack(tmp_path / "b", runs, hit, roots={"r1": "/work/another_app"})
    assert other.raw == () and other.empty and other.withheld.not_applicable == 1
    unrecorded = pack(tmp_path / "c", runs, hit)  # the run recorded no root at all
    assert unrecorded.raw == () and unrecorded.withheld.not_applicable == 1


def test_unconfirmed_hits_are_only_counted(tmp_path: Path) -> None:
    bad = UnverifiedHit(
        run_id="r9", step_id="s1", attempt_no=1, similarity=0.6, reason=SourceCheck.CHANGED
    )
    runs = {"r1": ([deps_step("r1", "s1", A, [B])], FP1)}
    got = pack(tmp_path, runs, Scripted([], unverified=[bad]))
    assert got.empty and got.withheld.unconfirmed == 1
    assert "r9" not in got.model_dump_json()  # nothing about it reaches an agent


def test_no_excerpt_and_no_request_text_leak_into_the_pack(tmp_path: Path) -> None:
    canary = "CANARY-q-7f3a"
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = make_db(tmp_path, {"r1": ([failed_step("r1", "s1")], FP1)}, {"r1": APP})
    with TraceStore.open_existing(db) as store:
        snap = build_from_traces(store)
        result = recall(
            canary,
            Scripted([("r1", "s1", 1, 0.9)], basis=None, status="failure"),
            store,
            snap,
            k=5,
            min_similarity=0.2,
        )
        assert result.experiences_without_claims[0].excerpt  # the source has an excerpt...
        got = build_context(canary, result, ExperienceGraph(snap), current_graph(), store, APP).pack
    text = got.model_dump_json()
    assert result.experiences_without_claims[0].excerpt not in text  # ...and it is not copied
    assert canary not in text and got.query_sha256 and len(got.query_sha256) == 64


def test_the_pack_is_bounded_and_says_what_it_dropped(tmp_path: Path) -> None:
    # the subject must exist in the current graph, so reuse A with many different objects
    objects = [f"module:src/dep_app/o{i}.py" for i in range(MAX_CLAIMS + 3)]
    runs = {"r1": ([deps_step("r1", "s1", A, objects)], FP1)}
    got = pack(tmp_path, runs, Scripted([("r1", "s1", 1, 0.9)]))
    assert len(got.claims) == MAX_CLAIMS and got.withheld.over_limit == 3


def test_building_twice_gives_the_same_pack(tmp_path: Path) -> None:
    runs = {"r1": ([deps_step("r1", "s1", A, [B, C])], FP1)}
    hits = Scripted([("r1", "s1", 1, 0.8)])
    assert pack(tmp_path / "x", runs, hits) == pack(tmp_path / "y", runs, hits)


def test_root_name_handles_both_separators() -> None:
    assert root_name("C:\\a\\b\\app") == root_name("/x/y/app") == "app"


def test_held_back_hits_are_listed_for_the_person_but_not_for_the_agents(tmp_path: Path) -> None:
    runs = {"r1": ([deps_step("r1", "s1", GONE, [B])], FP1)}
    got = gated(tmp_path, runs, Scripted([("r1", "s1", 1, 0.77)]))
    (held,) = got.not_applicable
    assert (held.kind, held.reason, held.similarity) == ("claim", REASON_SUBJECT, 0.77)
    assert GONE in held.label
    assert held.label not in got.pack.model_dump_json()  # the agents never see it
