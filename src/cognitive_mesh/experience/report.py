"""JSON payloads for `experience show`, `claims` and `check`, shared by the CLI and the web UI.

Pure functions over already-loaded data: nothing here opens a file or writes (ADR-0031 point 11).
The CLI `--json` output and the UI endpoints must stay identical, so both call these.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cognitive_mesh.experience.assess import STATUS_MEANING
from cognitive_mesh.experience.models import CLAIM_NOTE, Reached, SourceStatus
from cognitive_mesh.experience.verify import SourceReport, check_nodes

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from cognitive_mesh.experience.assess import ClaimAssessment
    from cognitive_mesh.experience.current import CurrentSource
    from cognitive_mesh.experience.graph import ExperienceGraph
    from cognitive_mesh.experience.models import ExpNode
    from cognitive_mesh.experience.reviews import Review
    from cognitive_mesh.experience.store import LoadedGraph
    from cognitive_mesh.traces import TraceStore


@dataclass(frozen=True)
class ShownNode:
    """A node and what it reaches, with the source status of each (ADR-0028 scope)."""

    node: ExpNode
    report: SourceReport
    reached: tuple[Reached, ...]
    shown: tuple[Reached, ...]
    hidden: int


def scoped_node_view(
    graph: ExperienceGraph,
    store: TraceStore,
    node_id: str,
    *,
    incoming: bool,
    transitive: bool,
    include_unverified: bool,
) -> ShownNode:
    """Check only the node and what it reaches, not the whole graph (ADR-0028)."""
    node = graph.node(node_id)
    reached = tuple(graph.reachable(node.id, forward=not incoming, transitive=transitive))
    report = SourceReport(
        statuses=check_nodes(graph, store, {node.id, *(r.node.id for r in reached)}),
        claim_support={},
        diagnostics=(),
    )
    shown = tuple(r for r in reached if include_unverified or report.is_supported(r.node.id))
    return ShownNode(node, report, reached, shown, len(reached) - len(shown))


def show_payload(view: ShownNode) -> dict[str, Any]:
    return {
        "node": view.node.model_dump(mode="json"),
        "status": view.report.statuses[view.node.id].value,
        "reached": [
            {
                "depth": r.depth,
                "status": view.report.statuses[r.node.id].value,
                "node": r.node.model_dump(mode="json"),
                "path": [e.model_dump(mode="json") for e in r.path],
            }
            for r in view.shown
        ],
        "hidden_unverified": view.hidden,
    }


@dataclass(frozen=True)
class ClaimsView:
    """Claim rows ready to print or serialise; `versions` is the code-version count per row."""

    rows: list[dict[str, Any]]
    versions: list[int]
    hidden: int
    meanings: dict[str, str]
    orphans: Sequence[Review]
    today: date
    current_fingerprint: str | None


def claims_view(
    graph: ExperienceGraph,
    report: SourceReport,
    assessed: dict[str, ClaimAssessment],
    orphans: Sequence[Review],
    current: CurrentSource | None,
    today: date,
    *,
    include_unverified: bool,
) -> ClaimsView:
    rows: list[dict[str, Any]] = []
    versions: list[int] = []
    hidden = 0
    for claim in graph.claims():
        status = report.statuses[claim.id]
        if status is not SourceStatus.OK and not include_unverified:
            hidden += 1
            continue
        evidence = graph.evidence(claim.id)
        prints = sorted(
            {r.source_fingerprint for e in evidence for r in e.refs if r.source_fingerprint}
        )
        rows.append(
            {
                "id": claim.id,
                "name": claim.name,
                "predicate": claim.predicate,
                "detail": claim.detail,
                "validation": claim.validation,
                "status": status.value,
                "observations": len(evidence),
                "still_confirmed": report.claim_support[claim.id],
                "source_fingerprints": prints,
                "evidence_status": assessed[claim.id].status.value,
                "assessment": assessed[claim.id].model_dump(mode="json"),
            }
        )
        versions.append(len(prints))
    shown_ids = {r["id"] for r in rows}
    shown = {a.status for a in assessed.values() if a.claim_id in shown_ids}
    meanings = {s.value: STATUS_MEANING[s] for s in sorted(shown, key=lambda s: s.value)}
    return ClaimsView(
        rows, versions, hidden, meanings, orphans, today, current.fingerprint if current else None
    )


def claims_payload(view: ClaimsView) -> dict[str, Any]:
    return {
        "claims": view.rows,
        "hidden_unverified": view.hidden,
        "current_fingerprint": view.current_fingerprint,
        "date_used": view.today.isoformat(),
        "orphaned_reviews": [r.model_dump(mode="json") for r in view.orphans],
        "status_meanings": view.meanings,
        "note": CLAIM_NOTE,
    }


def status_counts(report: SourceReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in report.statuses.values():
        counts[status.value] = counts.get(status.value, 0) + 1
    return counts


def check_payload(loaded: LoadedGraph, report: SourceReport) -> dict[str, Any]:
    return {
        "content_sha256": loaded.content_sha256,
        "built_at": loaded.built_at.isoformat(),
        "statuses": dict(sorted(status_counts(report).items())),
        "build_diagnostics": [d.model_dump(mode="json") for d in loaded.snapshot.diagnostics],
        "diagnostics": [d.model_dump(mode="json") for d in report.diagnostics],
    }
