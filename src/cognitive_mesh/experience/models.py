"""Experience graph data model (ADR-0015).

Nodes are experiences (runs, steps, final attempts), the things they touched, and claims their
deterministic outputs stated. Every edge carries at least one reference back to the trace database.
Claims are `unvalidated`: repetition across runs is counted, never treated as proof.
"""

import hashlib
import json
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.identity import IdentityHow
from cognitive_mesh.validity import Environment, EnvScope

FORMAT_VERSION: Final = 6
CLAIM_NOTE: Final = "claims are unvalidated: repeated observation is not proof of truth"
REPETITION_NOTE: Final = "repeated observation is counted, never treated as proof of truth"


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
    # Claims only (ADR-0019): the dependency-graph node id the claim is about, kept so recall can
    # find the claims of a subject without scanning the graph. Must equal the name of the
    # claim's `subject` target; a file where they differ is refused.
    subject: str | None = None
    # Attempts of deterministic listing capabilities only: was the listing cut off (ADR-0016)?
    coverage: Literal["complete", "truncated"] | None = None
    # Application identity (ADR-0021). Run, attempt and claim nodes carry `app_id` (None: run was
    # recorded without one). Only run nodes carry `app_id_how`; a claim merged from several runs
    # would otherwise depend on which run came first.
    app_id: str | None = None
    app_id_how: IdentityHow | None = None
    # ADR-0022. Claims: what the claim is valid for (None = unscoped; `build` always sets it).
    # Run nodes: the environment the analysis ran in. Claims of scope `environment` also carry
    # the environment they are valid for; none is produced yet.
    scope: EnvScope | None = None
    environment: Environment | None = None


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


def _dump_key(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=True)


def ref_key(ref: TraceRef) -> str:
    """The one ordering of references, used by the build, the hash and the file."""
    return _dump_key(ref.model_dump(mode="json"))


def node_text(node: ExpNode) -> str:
    """The stored text of a node: sorted keys, ASCII, compact. The hash covers these exact bytes."""
    return json.dumps(
        node.model_dump(mode="json"), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )


def diagnostic_text(diagnostic: ExperienceDiagnostic) -> str:
    return json.dumps(
        diagnostic.model_dump(mode="json"), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )


def edge_text(source: str, target: str, kind: str) -> str:
    return json.dumps([source, target, kind], ensure_ascii=True, separators=(",", ":"))


class ContentHasher:
    """SHA-256 over the stored record texts, each framed by a tag and its length (ADR-0027).

    The order is fixed: nodes by id, then edges by (source, target, kind) each followed by its
    references in `ref_key` order, then diagnostics in stored order. `save` and `load` feed this
    the very strings that are written and read, so every byte that is parsed is hashed.
    """

    def __init__(self) -> None:
        self._h = hashlib.sha256()
        self._put("F", str(FORMAT_VERSION))

    def _put(self, tag: str, text: str) -> None:
        data = text.encode("ascii")
        self._h.update(f"{tag}{len(data)}:".encode("ascii"))
        self._h.update(data)

    def node(self, text: str) -> None:
        self._put("N", text)

    def edge(self, source: str, target: str, kind: str) -> None:
        self._put("E", edge_text(source, target, kind))

    def ref(self, text: str) -> None:
        self._put("R", text)

    def diagnostic(self, text: str) -> None:
        self._put("D", text)

    def hexdigest(self) -> str:
        return self._h.hexdigest()


def content_sha256(
    nodes: tuple[ExpNode, ...],
    edges: tuple[ExpEdge, ...],
    diagnostics: tuple[ExperienceDiagnostic, ...] = (),
) -> str:
    """The content hash of a snapshot, independent of the order its nodes and edges came in."""
    hasher = ContentHasher()
    for n in sorted(nodes, key=lambda n: n.id):
        hasher.node(node_text(n))
    for e in sorted(edges, key=lambda e: (e.source, e.target, e.kind.value)):
        hasher.edge(e.source, e.target, e.kind.value)
        for text in sorted(ref_key(r) for r in e.refs):
            hasher.ref(text)
    for d in diagnostics:
        hasher.diagnostic(diagnostic_text(d))
    return hasher.hexdigest()
