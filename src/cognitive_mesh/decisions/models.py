"""Decision records (ADR-0039): what was decided, the options, the reasoning and the evidence cited.

A decision is an append-only record. Its reasoning is text somebody wrote: it is never evidence for
itself. A model-written decision is always `model_unverified`. Messages never contain given text.
"""

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cognitive_mesh.ingestion.secrets import redact_secrets

MAX_SUMMARY: Final = 300
MAX_OPTIONS: Final = 10
MAX_OPTION: Final = 200
MAX_CHOSEN: Final = 200
MAX_REASONING: Final = 4000
MAX_CITATIONS: Final = 50
MAX_LINK_VALUE: Final = 300
MAX_NAME: Final = 100
MAX_NODE_ID: Final = 400
MAX_PATH: Final = 400

DECISION_ID = re.compile(r"decision:[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NODE_ID = re.compile(r"[a-z]+:\S.*")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/#:@+-]*")


class DecisionError(Exception):
    """A decision or a decisions table cannot be used. Messages hold no given or stored text."""


class DuplicateDecisionError(DecisionError):
    """A decision with this exact id is already stored."""


class MadeBy(StrEnum):
    PERSON = "person"
    MODEL = "model"


class DecisionBasis(StrEnum):
    PERSON = "person"
    MODEL_UNVERIFIED = "model_unverified"


class CitationKind(StrEnum):
    NODE = "node"  # an experience-graph id (claim, target, run, ...)
    FILE = "file"  # a path under the application root, with the sha256 seen when recorded
    DECISION = "decision"  # an earlier decision of the same application


class LinkKind(StrEnum):
    COMMIT = "commit"
    BRANCH = "branch"
    PR = "pr"


class LinkHow(StrEnum):
    OBSERVED = "observed"  # read from the git repository at record time
    DECLARED = "declared"  # typed by a person; never checked


def clean_text(text: str, limit: int, what: str, *, multiline: bool = False) -> str:
    """Reject empty, control-character and over-long text, then redact secrets."""
    if not isinstance(text, str) or not text.strip():
        raise DecisionError(f"the decision {what} is empty")
    if len(text) > limit:
        raise DecisionError(f"the decision {what} is longer than {limit} characters")
    allowed = "\n\t" if multiline else ""
    if not all(ch.isprintable() or ch in allowed for ch in text):
        raise DecisionError(f"the decision {what} contains control characters")
    redacted, _ = redact_secrets(text.strip())
    return redacted


def clean_path(path: str) -> str:
    """A relative POSIX path without `..`, backslashes or control characters."""
    if (
        not isinstance(path, str)
        or not path
        or len(path) > MAX_PATH
        or not path.isprintable()
        or "\\" in path
        or path.startswith("/")
        or re.match(r"[A-Za-z]:", path)
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise DecisionError("a cited file path must be relative, POSIX and free of '..'")
    return path


def clean_node_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_NODE_ID
        or not value.isprintable()
        or not _NODE_ID.fullmatch(value)
    ):
        raise DecisionError("a cited node id is malformed")
    return value


def clean_link_value(kind: LinkKind, value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_LINK_VALUE
        or not _SAFE_REF.fullmatch(value)
    ):
        raise DecisionError(f"the {kind.value} reference is malformed")
    if kind is LinkKind.COMMIT and not re.fullmatch(r"[0-9a-f]{7,64}", value):
        raise DecisionError("a commit must be a hexadecimal commit id")
    redacted, _ = redact_secrets(value)
    if redacted != value:
        raise DecisionError(f"the {kind.value} reference looks like a secret")
    return value


class Citation(BaseModel):
    """One piece of evidence a decision cites; a file citation carries the sha256 seen then."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CitationKind
    target: str = Field(min_length=1, max_length=MAX_NODE_ID)  # node id, decision id, or file path
    sha256: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind is CitationKind.FILE:
            clean_path(self.target)
            if self.sha256 is None or not _SHA256.fullmatch(self.sha256):
                raise ValueError("a file citation needs the sha256 of the file")
        else:
            if self.sha256 is not None:
                raise ValueError("only a file citation carries a sha256")
            if self.kind is CitationKind.DECISION:
                if not DECISION_ID.fullmatch(self.target):
                    raise ValueError("a decision citation needs a decision id")
            else:
                clean_node_id(self.target)
        return self


class Decision(BaseModel):
    """A stored decision. `fingerprint` is the code version when recorded (None = unknown)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    app_id: str | None = None
    fingerprint: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    made_by: MadeBy
    recorder: str | None = Field(default=None, max_length=MAX_NAME)  # the person
    entry_id: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=100)
    agent_task_id: str | None = Field(default=None, max_length=300)
    basis: DecisionBasis
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    options: tuple[str, ...] = Field(max_length=MAX_OPTIONS)
    chosen: str = Field(min_length=1, max_length=MAX_CHOSEN)
    reasoning: str = Field(min_length=1, max_length=MAX_REASONING)
    citations: tuple[Citation, ...] = Field(max_length=MAX_CITATIONS)
    supersedes: str | None = None
    created_at: datetime
    redactions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not DECISION_ID.fullmatch(self.decision_id):
            raise ValueError("malformed decision id")
        if self.fingerprint is not None and not _SHA256.fullmatch(self.fingerprint):
            raise ValueError("malformed fingerprint")
        if self.step_id is not None and self.run_id is None:
            raise ValueError("a step needs its run_id")
        if self.supersedes is not None and not DECISION_ID.fullmatch(self.supersedes):
            raise ValueError("malformed superseded decision id")
        if self.supersedes == self.decision_id:
            raise ValueError("a decision cannot supersede itself")
        if self.made_by is MadeBy.PERSON:
            if self.basis is not DecisionBasis.PERSON or not self.recorder:
                raise ValueError("a person's decision names the person")
            if any((self.provider, self.model, self.role, self.agent_task_id, self.entry_id)):
                raise ValueError("a person's decision has no model fields")
        else:
            if self.basis is not DecisionBasis.MODEL_UNVERIFIED:
                raise ValueError("a model-written decision is model_unverified")
            if not (self.provider and self.model and self.role) or self.recorder:
                raise ValueError("a model's decision names provider, model and role")
        cited = [c.target for c in self.citations if c.kind is CitationKind.DECISION]
        if self.decision_id in cited:
            raise ValueError("a decision cannot cite itself")
        return self


class DecisionLink(BaseModel):
    """A link from a decision to a commit, branch or PR. `how` says whether anybody checked it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    decision_id: str
    kind: LinkKind
    value: str
    how: LinkHow
    recorder: str = Field(min_length=1, max_length=MAX_NAME)
    created_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if not DECISION_ID.fullmatch(self.decision_id):
            raise ValueError("malformed decision id")
        clean_link_value(self.kind, self.value)
        return self


class DecisionListing(BaseModel):
    """Every readable decision (oldest first) and the ids of rows that could not be read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: tuple[Decision, ...]
    links: tuple[DecisionLink, ...]
    malformed: tuple[str, ...] = ()


PLACEHOLDER_ID: Final = "decision:" + "0" * 32


def decision_digest(decision: Decision) -> str:
    """The id: `decision:` plus 32 hex of the sha256 of the canonical record (id left out).

    The id is recomputed on every read, so a row edited behind the program's back is malformed.
    """
    fields = decision.model_dump(mode="json", exclude={"decision_id"})
    text = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return "decision:" + hashlib.sha256(text.encode("ascii")).hexdigest()[:32]


def validation_failed(exc: ValidationError) -> DecisionError:
    """A fixed message; pydantic's own text can echo the given value."""
    fields = sorted({".".join(str(p) for p in e["loc"]) or "record" for e in exc.errors()})
    return DecisionError("the decision is not valid (" + ", ".join(fields) + ")")
