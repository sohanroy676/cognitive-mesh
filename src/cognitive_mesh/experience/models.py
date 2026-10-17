"""Experience graph data model (ADR-0015).

Nodes are experiences (runs, steps, final attempts), the things they touched, and claims their
deterministic outputs stated. Every edge carries at least one reference back to the trace database.
Claims are `unvalidated`: repetition across runs is counted, never treated as proof.
"""

import hashlib
import json
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT_VERSION: Final = 1
CLAIM_NOTE: Final = "claims are unvalidated: repeated observation is not proof of truth"


class ExperienceGraphError(Exception):
    """The graph file or its contents cannot be used. Messages never contain stored values."""


class ExperienceNodeNotFoundError(Exception):
    """A query named a node that is not in the graph."""


class NodeKind(StrEnum):
    RUN = "run"
    STEP = "step"
    ATTEMPT = "attempt"  # the final attempt of an eligible step (ADR-0014 eligibility)
    CAPABILITY = "capability"
    AGENT = "agent"
    TARGET = "target"  # what a step was about, e.g. a dependency-graph node id
    CLAIM = "claim"


class EdgeKind(StrEnum):
    HAS_STEP = "has_step"  # run -> step
    FINAL_ATTEMPT = "final_attempt"  # step -> attempt
    USED_CAPABILITY = "used_capability"  # attempt -> capability
    RAN_BY = "ran_by"  # attempt -> agent
    ABOUT = "about"  # attempt -> target
    EVIDENCED_BY = "evidenced_by"  # claim -> attempt
    SUBJECT = "subject"  # claim -> target
    OBJECT = "object"  # claim -> target


class SourceStatus(StrEnum):
    OK = "ok"
    MISSING = "source_missing"
    CHANGED = "source_changed"
    UNREADABLE = "source_unreadable"


class DiagnosticCode(StrEnum):
    MALFORMED_RUN = "malformed_run"
    RUN_NOT_FINISHED = "run_not_finished"  # interrupted runs contribute no nodes
    UNUSABLE_OUTPUT = "unusable_output"  # a deterministic output that does not match its schema
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    SOURCE_UNREADABLE = "source_unreadable"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TraceRef(_Frozen):
    """Where in the trace database a graph element comes from."""

    run_id: str = Field(min_length=1)
    step_id: str | None = None
    attempt_no: int | None = None
    text_sha256: str | None = None  # the ADR-0014 document hash of the final attempt
    source_fingerprint: str | None = None  # the run's fingerprint of the analysed application

    @property
    def doc_id(self) -> str | None:
        if self.step_id is None or self.attempt_no is None:
            return None
        return f"{self.run_id}/{self.step_id}/{self.attempt_no}"


class ExpNode(_Frozen):
    id: str
    kind: NodeKind
    name: str
    ref: TraceRef | None = None  # run, step and attempt nodes
    # Claims only: what is stated, and that nothing has validated it.
    predicate: Literal["depends_on", "is_unresolved"] | None = None
    detail: str | None = None  # depends_on: the kind of the dependency edge
    validation: Literal["unvalidated"] | None = None


class ExpEdge(_Frozen):
    source: str
    target: str
    kind: EdgeKind
    refs: tuple[TraceRef, ...] = Field(min_length=1)  # no edge without a source reference


class ExperienceDiagnostic(_Frozen):
    code: DiagnosticCode
    run_id: str | None = None
    node_id: str | None = None
    message: str  # fixed text, never stored values


class Snapshot(_Frozen):
    """Everything a build produced, in canonical (sorted) order."""

    nodes: tuple[ExpNode, ...]
    edges: tuple[ExpEdge, ...]
    diagnostics: tuple[ExperienceDiagnostic, ...] = ()


class Reached(_Frozen):
    node: ExpNode
    depth: int
    path: tuple[ExpEdge, ...]


def ref_key(ref: TraceRef) -> str:
    """The one ordering of references, used by the build, the hash and the file."""
    return json.dumps(ref.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)


def canonical(
    nodes: tuple[ExpNode, ...],
    edges: tuple[ExpEdge, ...],
    diagnostics: tuple[ExperienceDiagnostic, ...] = (),
) -> str:
    """The text the content hash covers: sorted, compact, ASCII, independent of insertion order."""
    node_part = [n.model_dump(mode="json") for n in sorted(nodes, key=lambda n: n.id)]
    edge_part = [
        {
            **e.model_dump(mode="json", exclude={"refs"}),
            "refs": [r.model_dump(mode="json") for r in sorted(e.refs, key=ref_key)],
        }
        for e in sorted(edges, key=lambda e: (e.source, e.target, e.kind.value))
    ]
    return json.dumps(
        {
            "format": FORMAT_VERSION,
            "nodes": node_part,
            "edges": edge_part,
            "diagnostics": [d.model_dump(mode="json") for d in diagnostics],
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def content_sha256(
    nodes: tuple[ExpNode, ...],
    edges: tuple[ExpEdge, ...],
    diagnostics: tuple[ExperienceDiagnostic, ...] = (),
) -> str:
    return hashlib.sha256(canonical(nodes, edges, diagnostics).encode("ascii")).hexdigest()
