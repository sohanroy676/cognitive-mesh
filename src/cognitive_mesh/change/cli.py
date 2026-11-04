"""The `change` commands (ADR-0042): propose a small change on a new branch, review it, approve it
and open a pull request. Nothing is pushed unless a person ran `change approve` and `change pr`.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cognitive_mesh.agents.registry import AgentRegistry, default_registry
from cognitive_mesh.change.checks import run_check
from cognitive_mesh.change.diffparse import DEFAULT_MAX_FILES, DEFAULT_MAX_LINES, Limits
from cognitive_mesh.change.errors import ChangeError
from cognitive_mesh.change.github import (
    PublishError,
    create_pull_request,
    origin_of,
    pr_title,
    push_branch,
)
from cognitive_mesh.change.models import (
    ChangeEvent,
    ChangeRecord,
    ChangeStatus,
    EventKind,
    approval_for,
    current_commit,
    derive_status,
    latest_check,
)
from cognitive_mesh.change.pipeline import ChangeRequest, ChangeResult, make_change
from cognitive_mesh.change.worktree import branch_head, remove_worktree, repo_layout
from cognitive_mesh.config import ConfigError, load_github_settings, load_settings
from cognitive_mesh.decisions.models import DecisionError, MadeBy
from cognitive_mesh.decisions.record import prepare_decision
from cognitive_mesh.experience.outcomes import OutcomeStoreError
from cognitive_mesh.ingestion import IngestionError
from cognitive_mesh.ingestion.secrets import redact_secrets
from cognitive_mesh.providers import CatalogError, build_model_client, catalog_entry_id
from cognitive_mesh.providers.recording import CallLog, RecordingClient
from cognitive_mesh.traces import TraceStore, TraceStoreError, utc_now
from cognitive_mesh.workspace import cli as workspace_cli
from cognitive_mesh.workspace.models import WorkspaceError
from cognitive_mesh.workspace.store import WorkspaceStore

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_TASK_NOT_SUCCEEDED = 7  # same codes as in cli.py (a test pins them)
EXIT_TRACE_STORE_ERROR = 8
EXIT_WORKSPACE_ERROR = 19
EXIT_CHANGE_ERROR = 24

_NOT_PUSHED = "nothing is pushed or merged; the branch is local"


def add_parser(sub: Any) -> None:
    change = sub.add_parser("change", help="make a small change on a new branch (ADR-0042)")
    cs = change.add_subparsers(dest="change_command", required=True)

    def db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", type=Path, required=True, help="the trace database")

    def as_json(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", dest="as_json")

    propose = cs.add_parser("propose", help="have a model draft a small change and check it")
    propose.add_argument("request", help="what to change (its text is not stored, only its length)")
    propose.add_argument("--root", type=Path, required=True, help="the current project's folder")
    propose.add_argument("--workspace", type=Path, required=True, help="the workspace file")
    propose.add_argument("--project", required=True, help="the current project's name")
    db(propose)
    propose.add_argument("--outcomes", type=Path, required=True, help="the outcomes file")
    propose.add_argument("--worktrees", type=Path, required=True, help="folder for the worktree")
    propose.add_argument("--file", action="append", default=[], dest="files", metavar="PATH")
    propose.add_argument("--base", default="HEAD", help="git ref to branch from (default HEAD)")
    propose.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    propose.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    propose.add_argument(
        "--allow-unisolated-checks",
        action="store_true",
        help="run the declared check command; it runs with your privileges and is NOT isolated",
    )
    propose.add_argument("--model", metavar="ID", help="a model catalog entry for the coder")
    propose.add_argument("--catalog", type=Path, metavar="FILE", help="model catalog file")
    as_json(propose)

    listing = cs.add_parser("list", help="list changes")
    db(listing)
    as_json(listing)
    show = cs.add_parser("show", help="show one change and its events")
    show.add_argument("change_id")
    db(show)
    as_json(show)

    approve = cs.add_parser("approve", help="approve the current commit of a change")
    approve.add_argument("change_id")
    db(approve)
    approve.add_argument("--root", type=Path, required=True, help="the project's folder")
    approve.add_argument("--by", required=True, help="who approves (an unauthenticated label)")
    approve.add_argument("--note", default=None, help="why (stored as the decision's reasoning)")

    pr = cs.add_parser("pr", help="push an approved change and open a pull request")
    pr.add_argument("change_id")
    db(pr)
    pr.add_argument("--root", type=Path, required=True, help="the project's folder")
    pr.add_argument("--base", default=None, help="branch for the pull request (default: its base)")
    pr.add_argument(
        "--without-passing-check",
        action="store_true",
        help="allow a change whose check did not pass; the description says so",
    )

    discard = cs.add_parser("discard", help="remove a change's worktree (the branch stays)")
    discard.add_argument("change_id")
    db(discard)
    discard.add_argument("--root", type=Path, required=True, help="the project's folder")


def run(args: argparse.Namespace) -> int:
    try:
        return _dispatch(args)
    except (ChangeError, DecisionError) as exc:
        print(f"change: {_plain(str(exc))}", file=sys.stderr)
        return EXIT_CHANGE_ERROR
    except WorkspaceError as exc:
        print(f"workspace: {_plain(str(exc))}", file=sys.stderr)
        return EXIT_WORKSPACE_ERROR
    except (TraceStoreError, OutcomeStoreError) as exc:
        print(f"change: {_plain(str(exc))}", file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    except (ConfigError, CatalogError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except IngestionError as exc:
        print(f"change: {_plain(str(exc))}", file=sys.stderr)
        return EXIT_CHANGE_ERROR


def _plain(text: str) -> str:
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _dispatch(args: argparse.Namespace) -> int:
    command = args.change_command
    if command == "propose":
        return _propose(args)
    if command == "approve":
        return _approve(args)
    if command == "pr":
        return _pr(args)
    if command == "discard":
        return _discard(args)
    with TraceStore.open_existing(args.db) as store:
        changes = store.read_changes()
    if command == "list":
        return _list(args, changes)
    return _show(args, _find(changes, args.change_id), changes)


# -- propose ---------------------------------------------------------------------------------


def _registry_factory(args: argparse.Namespace) -> Any:
    overrides: dict[str, Any] = {}
    if args.model is not None:
        overrides.update(provider="catalog", model=args.model)
    if args.catalog is not None:
        overrides["model_catalog"] = args.catalog
    settings = load_settings(**overrides)
    base = build_model_client(settings, ["coder"])
    entry = catalog_entry_id(settings)

    def make(calls: CallLog) -> AgentRegistry:
        return default_registry(
            RecordingClient(base, calls, entry), model_timeout_s=settings.model_timeout_s
        )

    return make


def _propose(args: argparse.Namespace) -> int:
    try:
        limits = Limits(max_files=args.max_files, max_lines=args.max_lines)
    except ValueError as exc:
        raise ChangeError(str(exc)) from None
    access = workspace_cli.build_access(args.workspace, args.project, args.root)
    with WorkspaceStore.open_existing(args.workspace) as ws:
        check = ws.check_command(args.project)
    factory = _registry_factory(args)
    notes: list[str] = []
    if check is None:
        notes.append("no check command is declared for this project: the change will be unchecked")
    elif not args.allow_unisolated_checks:
        notes.append("the declared check will not run without --allow-unisolated-checks")
    with TraceStore.create(args.db) as store:
        result = make_change(
            ChangeRequest(
                request=args.request,
                project=args.project,
                files=tuple(args.files),
                limits=limits,
                worktrees=args.worktrees,
                base_ref=args.base,
                allow_unisolated_checks=args.allow_unisolated_checks,
            ),
            access=access,
            check=check,
            registry_factory=factory,
            store=store,
            outcomes=args.outcomes,
            runner=run_check,
        )
    _print_result(result, notes, args.as_json)
    ok = result.status in (ChangeStatus.CHECK_PASSED, ChangeStatus.UNCHECKED)
    return EXIT_OK if ok else EXIT_TASK_NOT_SUCCEEDED


def _print_result(result: ChangeResult, notes: list[str], as_json: bool) -> None:
    change = result.change
    if as_json:
        payload = {
            "change_id": change.change_id,
            "branch": change.branch,
            "worktree": change.worktree,
            "base_commit": change.base_commit,
            "status": result.status.value,
            "drafts": [vars(d) for d in result.drafts],
            "notes": [*notes, *result.notes],
            "pushed": False,
        }
        print(json.dumps(payload, indent=2))
        return
    print(f"change {change.change_id}: {result.status.value}")
    print(f"  branch {change.branch} (worktree {_plain(change.worktree)})")
    for d in result.drafts:
        extra = f" commit {d.commit[:12]}" if d.commit else ""
        print(f"  draft {d.draft_no}: {d.outcome}{extra}")
    for note in [*notes, *result.notes]:
        print(f"  note: {_plain(note)}")
    print("  the diff and its description were written by a model and are not verified")
    if result.status is ChangeStatus.UNCHECKED:
        print("  no check ran: nothing validates this change yet")
    print(f"  {_NOT_PUSHED}; review it, then `change approve` and `change pr`")


# -- reading ---------------------------------------------------------------------------------


Changes = list[tuple[ChangeRecord, list[ChangeEvent]]]


def _find(changes: Changes, change_id: str) -> tuple[ChangeRecord, list[ChangeEvent]]:
    for record, events in changes:
        if record.change_id == change_id:
            return record, events
    raise ChangeError("no change has that id")


def _list(args: argparse.Namespace, changes: Changes) -> int:
    rows = [
        {
            "change_id": r.change_id,
            "project": r.project,
            "branch": r.branch,
            "status": derive_status(e).value,
            "created_at": r.created_at.isoformat(),
        }
        for r, e in changes
    ]
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    if not rows:
        print("no changes recorded")
    for row in rows:
        print(f"{row['change_id']}  {row['status']:<13} {row['project']}  {row['branch']}")
    return EXIT_OK


def _show(
    args: argparse.Namespace, found: tuple[ChangeRecord, list[ChangeEvent]], _all: Changes
) -> int:
    record, events = found
    status = derive_status(events)
    if args.as_json:
        payload = {
            **record.model_dump(mode="json"),
            "status": status.value,
            "events": [e.model_dump(mode="json") for e in events],
        }
        print(json.dumps(payload, indent=2))
        return EXIT_OK
    print(f"change {record.change_id}: {status.value}")
    print(f"  project {record.project}, branch {record.branch}, base {record.base_commit[:12]}")
    print(f"  limits: {record.max_files} file(s), {record.max_lines} changed line(s)")
    for e in events:
        commit = f" {e.commit[:12]}" if e.commit else ""
        ref = f" [{e.ref}]" if e.ref else ""
        print(f"  {e.created_at.isoformat()} {e.kind.value}{commit}{ref} {_plain(e.detail)}")
    return EXIT_OK


# -- approve, pr, discard --------------------------------------------------------------------


def _head(record: ChangeRecord, events: list[ChangeEvent], root: Path) -> ChangeEvent:
    """The applied draft, if the branch still points at exactly that commit."""
    head = current_commit(events)
    if head is None or head.commit is None or head.diff_sha256 is None:
        raise ChangeError("the change has no applied draft")
    top, _ = repo_layout(root)
    if branch_head(top, record.branch) != head.commit:
        raise ChangeError("the branch no longer points at the recorded commit; nothing is done")
    return head


def _approve(args: argparse.Namespace) -> int:
    with TraceStore.create(args.db) as store:
        record, events = _find(store.read_changes(), args.change_id)
        head = _head(record, events, args.root)
        assert head.commit is not None and head.diff_sha256 is not None  # noqa: S101 - _head
        reasoning = args.note or "approved after reading the diff"
        decision = prepare_decision(
            summary=f"Approve change {record.change_id} at commit {head.commit[:12]}",
            options=["approve this commit", "do not approve"],
            chosen="approve this commit",
            reasoning=f"{reasoning} (diff sha256 {head.diff_sha256})",
            made_by=MadeBy.PERSON,
            recorder=args.by,
            cite_decisions=[head.ref] if head.ref else [],
            app_id=record.app_id,
            now=utc_now(),
        )
        store.add_decision(decision)
        store.add_change_event(
            ChangeEvent(
                change_id=record.change_id,
                kind=EventKind.APPROVED,
                commit=head.commit,
                diff_sha256=head.diff_sha256,
                ref=decision.decision_id,
                detail="approved by a person",
                created_at=utc_now(),
            )
        )
    print(
        f"approved change {record.change_id} at {head.commit[:12]} "
        f"(decision {decision.decision_id})"
    )
    print("an approval is a person's label, not a check; it is not pushed yet (`change pr`)")
    return EXIT_OK


def _pr_body(
    record: ChangeRecord, events: list[ChangeEvent], head: ChangeEvent, summary: str, passed: bool
) -> str:
    impact = [e for e in events if e.kind is EventKind.IMPACT and e.diff_sha256 == head.diff_sha256]
    check = latest_check(events, head.commit or "")
    approval = approval_for(events, head.commit or "", head.diff_sha256 or "")
    if check is None:
        check_line = "no check ran for this commit"
    else:
        check_line = f"{check.detail} (the check was not isolated; it ran with the author's rights)"
    if check is not None and not passed:
        check_line += "; this change was published without a passing check"
    lines = [
        f"Change `{record.change_id}`, drafted by a model through cognitive-mesh. "
        "Read the diff before merging; nothing here was merged by the tool.",
        "",
        f"**What the model said it did (unverified):** {summary}",
        "",
        f"- Base: `{record.base_branch or 'detached'}` at `{record.base_commit}`",
        f"- Commit: `{head.commit}`; diff sha256 `{head.diff_sha256}`",
        f"- Contract check: {impact[-1].detail if impact else 'not recorded'}",
        f"- Declared check: {check_line}",
        f"- Draft decision: `{head.ref}`" if head.ref else "- Draft decision: not recorded",
        f"- Approved by a person: decision `{approval.ref}`" if approval else "",
    ]
    text, _ = redact_secrets("\n".join(line for line in lines if line is not None))
    return text


def _pr(args: argparse.Namespace) -> int:
    with TraceStore.create(args.db) as store:
        record, events = _find(store.read_changes(), args.change_id)
        head = _head(record, events, args.root)
        assert head.commit is not None and head.diff_sha256 is not None  # noqa: S101 - _head
        opened = [e for e in events if e.kind is EventKind.PR_OPENED and e.commit == head.commit]
        if opened:
            print(f"a pull request is already open for this commit: {opened[-1].ref}")
            return EXIT_OK
        if approval_for(events, head.commit, head.diff_sha256) is None:
            raise ChangeError("no approval names the current commit and diff; run `change approve`")
        check = latest_check(events, head.commit)
        passed = check is not None and check.detail.startswith("succeeded")
        if not passed and not args.without_passing_check:
            raise ChangeError(
                "the latest check did not pass (or none ran); fix it, or pass "
                "--without-passing-check"
            )
        github = load_github_settings()
        if not github.allow_remote_content:
            raise ChangeError(
                "COGMESH_ALLOW_REMOTE_CONTENT=true is required: the diff and its description "
                "go to GitHub"
            )
        top, _ = repo_layout(args.root)
        owner, repo = origin_of(top)
        base = args.base or record.base_branch
        if base is None:
            raise ChangeError("the change started from a detached HEAD; give --base BRANCH")
        summary = _draft_summary(store, head.ref)

        def note(kind: EventKind, detail: str, ref: str | None = None) -> None:
            store.add_change_event(
                ChangeEvent(
                    change_id=record.change_id,
                    kind=kind,
                    commit=head.commit,
                    diff_sha256=head.diff_sha256,
                    ref=ref,
                    detail=detail,
                    created_at=utc_now(),
                )
            )

        already = any(e.kind is EventKind.PUSHED and e.commit == head.commit for e in events)
        if not already:
            try:
                push_branch(top, owner, repo, record.branch, github.github_token)
            except PublishError as exc:
                note(EventKind.PUSH_FAILED, exc.failure.value)
                raise
            note(EventKind.PUSHED, f"pushed {record.branch} to {owner}/{repo}")
        try:
            request = create_pull_request(
                owner,
                repo,
                record.branch,
                base,
                pr_title(summary),
                _pr_body(record, events, head, summary, passed),
                github.github_token,
            )
        except PublishError as exc:
            note(EventKind.PR_FAILED, exc.failure.value)
            print(f"the branch is pushed to {owner}/{repo}; `change pr` can be run again")
            raise
        note(EventKind.PR_OPENED, f"pull request #{request.number}", request.url)
    print(f"pull request opened: {request.url}")
    print("nothing was merged; review it on GitHub")
    return EXIT_OK


def _draft_summary(store: TraceStore, decision_id: str | None) -> str:
    if decision_id is None:
        return "no summary was recorded"
    for decision in store.read_decisions().decisions:
        if decision.decision_id == decision_id:
            return decision.summary
    return "no summary was recorded"


def _discard(args: argparse.Namespace) -> int:
    with TraceStore.create(args.db) as store:
        record, events = _find(store.read_changes(), args.change_id)
        if EventKind.DISCARDED in {e.kind for e in events}:
            raise ChangeError("the worktree was already removed")
        remove_worktree(args.root, Path(record.worktree))
        store.add_change_event(
            ChangeEvent(
                change_id=record.change_id,
                kind=EventKind.DISCARDED,
                detail="worktree removed; the branch was kept",
                created_at=utc_now(),
            )
        )
    print(f"removed the worktree of change {record.change_id}; branch {record.branch} is kept")
    return EXIT_OK
