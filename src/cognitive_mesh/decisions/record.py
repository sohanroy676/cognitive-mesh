"""Record a decision (ADR-0039): validate, resolve citations against the application, store.

The one function a person's CLI call and the v1.10.0 conductor both use. It reads files under the
root (never executes them), stores the sha256 it saw for each cited file, and reads the commit and
branch of the root when it is a git repository (stored as `observed`).
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from cognitive_mesh.decisions.models import (
    MAX_CHOSEN,
    MAX_OPTION,
    MAX_OPTIONS,
    MAX_REASONING,
    MAX_SUMMARY,
    PLACEHOLDER_ID,
    Citation,
    CitationKind,
    Decision,
    DecisionBasis,
    DecisionError,
    DecisionLink,
    DuplicateDecisionError,
    LinkHow,
    LinkKind,
    MadeBy,
    clean_node_id,
    clean_path,
    clean_text,
    decision_digest,
    validation_failed,
)
from cognitive_mesh.identity import IdentityError, derive_identity, validate_app_id
from cognitive_mesh.ingestion import IngestionError, ingest_application
from cognitive_mesh.traces import TraceStore, source_fingerprint
from cognitive_mesh.workspace.fetch import FetchError, run_git

MAX_CLOCK_RETRIES = 20
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/#+-]{0,299}")


class FileView:
    """The application's files as they are now: the code version and a sha256 per path."""

    def __init__(self, fingerprint: str, hashes: dict[str, str]) -> None:
        self.fingerprint = fingerprint
        self.hashes = hashes


def observe_files(root: Path) -> FileView:
    """Ingest `root` read-only. Raises DecisionError when the root cannot be used."""
    try:
        result = ingest_application(root)
    except (IngestionError, OSError):
        raise DecisionError("the application root cannot be read") from None
    return FileView(source_fingerprint(result), {f.ref.path: f.ref.sha256 for f in result.files})


def observed_git_refs(root: Path) -> list[tuple[LinkKind, str]]:
    """The commit and branch of `root` if it is a git work tree; nothing otherwise.

    An observed commit says nothing about uncommitted changes: the fingerprint is the evidence
    about the code. A detached HEAD records the commit only.
    """
    refs: list[tuple[LinkKind, str]] = []
    try:
        head = run_git(["rev-parse", "HEAD"], root)
        if head.returncode != 0 or not _COMMIT.fullmatch(head.stdout.strip()):
            return refs
        refs.append((LinkKind.COMMIT, head.stdout.strip()))
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    except FetchError:
        return refs
    name = branch.stdout.strip()
    if branch.returncode == 0 and name != "HEAD" and _BRANCH.fullmatch(name):
        refs.append((LinkKind.BRANCH, name))
    return refs


def prepare_decision(
    *,
    summary: str,
    options: Sequence[str],
    chosen: str,
    reasoning: str,
    made_by: MadeBy,
    recorder: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    role: str | None = None,
    entry_id: str | None = None,
    agent_task_id: str | None = None,
    cite_nodes: Sequence[str] = (),
    cite_files: Sequence[str] = (),
    cite_decisions: Sequence[str] = (),
    supersedes: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    root: Path | None = None,
    view: FileView | None = None,
    app_id: str | None = None,
    now: datetime | None = None,
) -> Decision:
    """Validate one decision and give it its id, without storing it (ADR-0040).

    `view` is a file view the caller already took (so several decisions of one run ingest the
    root once); with `root` and no `view` the root is ingested here. Raises `DecisionError`
    (fixed messages) for anything invalid.
    """
    if len(options) > MAX_OPTIONS:
        raise DecisionError(f"a decision has at most {MAX_OPTIONS} options")
    redactions: list[str] = []

    def text(value: str, limit: int, what: str, *, multiline: bool = False) -> str:
        cleaned = clean_text(value, limit, what, multiline=multiline)
        if cleaned != value.strip():
            redactions.append(what)
        return cleaned

    clean_options = tuple(text(o, MAX_OPTION, "option") for o in options)
    clean_summary = text(summary, MAX_SUMMARY, "summary")
    clean_chosen = text(chosen, MAX_CHOSEN, "chosen option")
    clean_reasoning = text(reasoning, MAX_REASONING, "reasoning", multiline=True)

    if cite_files and root is None and view is None:
        raise DecisionError("citing a file needs --root")
    if view is None and root is not None:
        view = observe_files(root)
    identity_id = _app_id(root, app_id)

    citations: list[Citation] = [
        Citation(kind=CitationKind.NODE, target=clean_node_id(n)) for n in cite_nodes
    ]
    for raw in cite_files:
        path = clean_path(raw)
        digest = view.hashes.get(path) if view is not None else None
        if digest is None:
            raise DecisionError("a cited file was not found under the root")
        citations.append(Citation(kind=CitationKind.FILE, target=path, sha256=digest))
    citations += [Citation(kind=CitationKind.DECISION, target=d) for d in cite_decisions]
    seen = set()
    for cite in citations:
        key = (cite.kind, cite.target)
        if key in seen:
            raise DecisionError("the same evidence is cited twice")
        seen.add(key)

    created = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        draft = Decision(
            decision_id=PLACEHOLDER_ID,
            app_id=identity_id,
            fingerprint=view.fingerprint if view is not None else None,
            run_id=run_id,
            step_id=step_id,
            made_by=made_by,
            recorder=recorder,
            entry_id=entry_id,
            provider=provider,
            model=model,
            role=role,
            agent_task_id=agent_task_id,
            basis=(
                DecisionBasis.PERSON if made_by is MadeBy.PERSON else DecisionBasis.MODEL_UNVERIFIED
            ),
            summary=clean_summary,
            options=clean_options,
            chosen=clean_chosen,
            reasoning=clean_reasoning,
            citations=tuple(citations),
            supersedes=supersedes,
            created_at=created,
            redactions=tuple(sorted(set(redactions))),
        )
    except ValidationError as exc:
        raise validation_failed(exc) from None
    return _stamped(draft, created)


def _stamped(draft: Decision, created: datetime) -> Decision:
    """The decision at time `created`, with the id of that exact record."""
    timed = draft.model_copy(update={"created_at": created})
    return timed.model_copy(update={"decision_id": decision_digest(timed)})


def record_decision(
    store: TraceStore,
    *,
    summary: str,
    options: Sequence[str],
    chosen: str,
    reasoning: str,
    made_by: MadeBy,
    recorder: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    role: str | None = None,
    entry_id: str | None = None,
    agent_task_id: str | None = None,
    cite_nodes: Sequence[str] = (),
    cite_files: Sequence[str] = (),
    cite_decisions: Sequence[str] = (),
    supersedes: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    root: Path | None = None,
    app_id: str | None = None,
    now: datetime | None = None,
) -> tuple[Decision, tuple[DecisionLink, ...]]:
    """Validate and store one decision. Returns it with the observed links that were stored.

    Raises `DecisionError` (fixed messages) for anything invalid; nothing is stored then.
    """
    first = prepare_decision(
        summary=summary,
        options=options,
        chosen=chosen,
        reasoning=reasoning,
        made_by=made_by,
        recorder=recorder,
        provider=provider,
        model=model,
        role=role,
        entry_id=entry_id,
        agent_task_id=agent_task_id,
        cite_nodes=cite_nodes,
        cite_files=cite_files,
        cite_decisions=cite_decisions,
        supersedes=supersedes,
        run_id=run_id,
        step_id=step_id,
        root=root,
        app_id=app_id,
        now=now,
    )
    created = first.created_at
    # The id covers the record including its time, so two identical records differ by their
    # clock reading; a coarse clock can repeat, so a duplicate id is retried a microsecond later.
    for attempt in range(MAX_CLOCK_RETRIES + 1):
        decision = _stamped(first, created + timedelta(microseconds=attempt))
        try:
            store.add_decision(decision)
            break
        except DuplicateDecisionError:
            if attempt == MAX_CLOCK_RETRIES:
                raise
    created = decision.created_at

    links: list[DecisionLink] = []
    if root is not None:
        for kind, value in observed_git_refs(root):
            link = DecisionLink(
                decision_id=decision.decision_id,
                kind=kind,
                value=value,
                how=LinkHow.OBSERVED,
                recorder=recorder or role or "git",
                created_at=created,
            )
            store.add_decision_link(link)
            links.append(link)
    return decision, tuple(links)


def _app_id(root: Path | None, declared: str | None) -> str | None:
    try:
        if root is None:
            return validate_app_id(declared) if declared is not None else None
        identity = derive_identity(str(root), declared)
    except IdentityError as exc:
        raise DecisionError(str(exc)) from None
    return identity.app_id if identity is not None else None
