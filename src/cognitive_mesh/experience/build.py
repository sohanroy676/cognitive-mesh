"""Build the experience graph from the trace database (ADR-0015). Pure over what the store returns.

Experiences come from the same eligible documents as the vector index (ADR-0014), so an attempt
node and an index entry share the key `run_id/step_id/attempt_no` and the text hash. Claims come
only from successful steps whose output is deterministic; model output never becomes a claim.
"""

import hashlib
from typing import Final, Literal

from pydantic import ValidationError

from cognitive_mesh.agents import AgentSuccess
from cognitive_mesh.agents.dependency_analyst import DependenciesOfOutput, UnresolvedReportOutput
from cognitive_mesh.agents.models import Basis
from cognitive_mesh.experience.models import (
    DiagnosticCode,
    EdgeKind,
    ExpEdge,
    ExperienceDiagnostic,
    ExpNode,
    NodeKind,
    Snapshot,
    TraceRef,
    ref_key,
)
from cognitive_mesh.memory.documents import eligible_documents
from cognitive_mesh.traces import RunTrace, TraceRecordError, TraceStatus, TraceStore

MAX_ID_TEXT: Final = 160


def _clip(text: str) -> str:
    """Keep ids short but unique: long text is cut and gets a hash of the whole."""
    if len(text) <= MAX_ID_TEXT:
        return text
    return f"{text[:MAX_ID_TEXT]}~{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def claim_id(predicate: str, subject: str, obj: str, detail: str) -> str:
    key = "\x1f".join((predicate, subject, obj, detail))
    return f"claim:{hashlib.sha256(key.encode()).hexdigest()[:32]}"


class _Accumulator:
    """Merges duplicate nodes and edges; an edge seen again only adds its reference."""

    def __init__(self) -> None:
        self.nodes: dict[str, ExpNode] = {}
        self.edges: dict[tuple[str, str, EdgeKind], list[TraceRef]] = {}

    def node(self, node: ExpNode) -> None:
        self.nodes.setdefault(node.id, node)

    def edge(self, source: str, target: str, kind: EdgeKind, ref: TraceRef) -> None:
        refs = self.edges.setdefault((source, target, kind), [])
        if ref not in refs:
            refs.append(ref)

    def snapshot(self, diagnostics: list[ExperienceDiagnostic]) -> Snapshot:
        nodes = tuple(sorted(self.nodes.values(), key=lambda n: n.id))
        edges = tuple(
            ExpEdge(source=s, target=t, kind=k, refs=tuple(sorted(refs, key=ref_key)))
            for (s, t, k), refs in sorted(
                self.edges.items(), key=lambda i: (i[0][0], i[0][1], i[0][2].value)
            )
        )
        return Snapshot(nodes=nodes, edges=edges, diagnostics=tuple(diagnostics))


def build_from_traces(store: TraceStore) -> Snapshot:
    """Read every run once. Malformed and unfinished runs become diagnostics, not nodes."""
    acc = _Accumulator()
    diagnostics: list[ExperienceDiagnostic] = []
    listing = store.list()
    for run_id in sorted(listing.malformed):
        diagnostics.append(
            ExperienceDiagnostic(
                code=DiagnosticCode.MALFORMED_RUN,
                run_id=run_id,
                message="the run is malformed in the trace database and was skipped",
            )
        )
    for summary in sorted(listing.runs, key=lambda r: r.run_id):
        try:
            trace = store.get(summary.run_id)
        except TraceRecordError:
            diagnostics.append(
                ExperienceDiagnostic(
                    code=DiagnosticCode.MALFORMED_RUN,
                    run_id=summary.run_id,
                    message="the run is malformed in the trace database and was skipped",
                )
            )
            continue
        _add_run(acc, trace, diagnostics)
    return acc.snapshot(diagnostics)


def _add_run(acc: _Accumulator, trace: RunTrace, diagnostics: list[ExperienceDiagnostic]) -> None:
    if trace.status is TraceStatus.INTERRUPTED:
        diagnostics.append(
            ExperienceDiagnostic(
                code=DiagnosticCode.RUN_NOT_FINISHED,
                run_id=trace.run_id,
                message="the run never finished and was skipped",
            )
        )
        return
    docs = {d.doc_id: d for d in eligible_documents(trace)}
    if not docs:
        return
    run_node = f"run:{trace.run_id}"
    acc.node(
        ExpNode(
            id=run_node,
            kind=NodeKind.RUN,
            name=trace.task_id,
            ref=TraceRef(run_id=trace.run_id, source_fingerprint=trace.source_fingerprint),
        )
    )
    for step in trace.steps:
        if not step.attempts:
            continue
        final = step.attempts[-1]
        doc = docs.get(f"{trace.run_id}/{step.step_id}/{final.attempt_no}")
        if doc is None:
            continue
        ref = TraceRef(
            run_id=trace.run_id,
            step_id=step.step_id,
            attempt_no=final.attempt_no,
            text_sha256=doc.text_sha256,
            source_fingerprint=trace.source_fingerprint,
        )
        step_node = f"step:{trace.run_id}/{step.step_id}"
        attempt_node = f"attempt:{doc.doc_id}"
        acc.node(
            ExpNode(
                id=step_node,
                kind=NodeKind.STEP,
                name=step.step_id,
                ref=ref.model_copy(update={"attempt_no": None, "text_sha256": None}),
            )
        )
        result = final.result
        acc.node(
            ExpNode(
                id=attempt_node,
                kind=NodeKind.ATTEMPT,
                name=doc.doc_id,
                ref=ref,
                coverage=_coverage(step.capability, result),
            )
        )
        acc.edge(
            run_node,
            step_node,
            EdgeKind.HAS_STEP,
            ref.model_copy(update={"step_id": None, "attempt_no": None, "text_sha256": None}),
        )
        acc.edge(step_node, attempt_node, EdgeKind.FINAL_ATTEMPT, ref)
        cap = f"capability:{_clip(step.capability)}"
        acc.node(ExpNode(id=cap, kind=NodeKind.CAPABILITY, name=step.capability))
        acc.edge(attempt_node, cap, EdgeKind.USED_CAPABILITY, ref)
        agent = f"agent:{_clip(final.agent_id)}"
        acc.node(ExpNode(id=agent, kind=NodeKind.AGENT, name=final.agent_id))
        acc.edge(attempt_node, agent, EdgeKind.RAN_BY, ref)
        if isinstance(result, AgentSuccess) and result.basis is Basis.DETERMINISTIC:
            _add_claims(acc, trace, step.capability, result, attempt_node, ref, diagnostics)


def _coverage(capability: str, result: object) -> Literal["complete", "truncated"] | None:
    """Whether a deterministic listing was cut off (ADR-0016); None if it says nothing on absence.

    An output that does not match its schema gets None here; `_add_claims` reports it.
    """
    if not isinstance(result, AgentSuccess) or result.basis is not Basis.DETERMINISTIC:
        return None
    try:
        if capability == "dependencies_of":
            truncated = DependenciesOfOutput.model_validate(result.output).truncated
        elif capability == "unresolved_report":
            truncated = UnresolvedReportOutput.model_validate(result.output).truncated
        else:
            return None
    except ValidationError:
        return None
    return "truncated" if truncated else "complete"


def _target(acc: _Accumulator, node_id: str) -> str:
    tid = f"target:{_clip(node_id)}"
    acc.node(ExpNode(id=tid, kind=NodeKind.TARGET, name=node_id))
    return tid


def _add_claims(
    acc: _Accumulator,
    trace: RunTrace,
    capability: str,
    result: AgentSuccess,
    attempt_node: str,
    ref: TraceRef,
    diagnostics: list[ExperienceDiagnostic],
) -> None:
    try:
        if capability == "dependencies_of":
            deps = DependenciesOfOutput.model_validate(result.output)
            subject = _target(acc, deps.node_id)
            acc.edge(attempt_node, subject, EdgeKind.ABOUT, ref)
            for item in deps.dependencies:
                if item.depth != 1:  # only direct dependencies are claims
                    continue
                obj = _target(acc, item.node_id)
                cid = claim_id("depends_on", deps.node_id, item.node_id, item.edge_kind)
                acc.node(
                    ExpNode(
                        id=cid,
                        kind=NodeKind.CLAIM,
                        name=f"{deps.node_id} depends_on {item.node_id}",
                        predicate="depends_on",
                        detail=item.edge_kind,
                        validation="unvalidated",
                        subject=deps.node_id,
                    )
                )
                acc.edge(cid, attempt_node, EdgeKind.EVIDENCED_BY, ref)
                acc.edge(cid, subject, EdgeKind.SUBJECT, ref)
                acc.edge(cid, obj, EdgeKind.OBJECT, ref)
        elif capability == "unresolved_report":
            report = UnresolvedReportOutput.model_validate(result.output)
            for entry in report.unresolved:
                subject = _target(acc, entry.node_id)
                cid = claim_id("is_unresolved", entry.node_id, "", "")
                acc.node(
                    ExpNode(
                        id=cid,
                        kind=NodeKind.CLAIM,
                        name=f"{entry.node_id} is_unresolved",
                        predicate="is_unresolved",
                        validation="unvalidated",
                        subject=entry.node_id,
                    )
                )
                acc.edge(cid, attempt_node, EdgeKind.EVIDENCED_BY, ref)
                acc.edge(cid, subject, EdgeKind.SUBJECT, ref)
    except ValidationError:
        diagnostics.append(
            ExperienceDiagnostic(
                code=DiagnosticCode.UNUSABLE_OUTPUT,
                run_id=trace.run_id,
                node_id=attempt_node,
                message="the output does not match its schema; no claims were taken from it",
            )
        )
