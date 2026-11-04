"""The `decisions` commands (ADR-0039): record, link, list, show and trace decision records.

`record` and `link` write to the trace database; the others are read-only. A decision's reasoning
is what its author wrote: it is printed as data and never counted as evidence for itself.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cognitive_mesh.decisions.models import (
    DECISION_ID,
    Decision,
    DecisionError,
    DecisionLink,
    LinkHow,
    LinkKind,
    MadeBy,
)
from cognitive_mesh.decisions.record import record_decision
from cognitive_mesh.experience.decision_assess import DecisionAssessment
from cognitive_mesh.experience.decision_view import (
    DecisionsView,
    assess_all,
    decision_payload,
    history_of,
    matching_links,
)
from cognitive_mesh.traces import TraceStore, TraceStoreError, utc_now

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INGESTION_ERROR = 3
EXIT_TRACE_STORE_ERROR = 8  # same codes as in cli.py (a test pins them)
EXIT_EXPERIENCE_ERROR = 12
EXIT_NOT_FOUND = 13
EXIT_REVIEW_STORE_ERROR = 15
EXIT_OUTCOME_STORE_ERROR = 16
EXIT_DECISION_ERROR = 21

MAX_JSON_BYTES = 256 * 1024
_CONTENT_FLAGS = (
    "summary",
    "option",
    "chosen",
    "reasoning",
    "cite_node",
    "cite_file",
    "cite_decision",
    "supersedes",
    "run",
    "step",
)
NOTE = (
    "reasoning is what the author wrote, not evidence; a status describes the cited evidence "
    "and recorded outcomes for one code version, and is not proof that the decision was right"
)


def _add_read_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    parser.add_argument("--graph", type=Path, metavar="FILE", help="experience graph file")
    parser.add_argument("--reviews", type=Path, metavar="FILE", help="reviews file")
    parser.add_argument("--outcomes", type=Path, metavar="FILE", help="outcomes file")
    parser.add_argument(
        "--root", type=Path, metavar="DIR", help="application root, to check cited files and code"
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")


def add_parser(sub: Any) -> None:
    decisions = sub.add_parser("decisions", help="record and inspect decision records (ADR-0039)")
    d_sub = decisions.add_subparsers(dest="decisions_command", required=True)

    rec = d_sub.add_parser("record", help="record a decision made by a person")
    rec.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    rec.add_argument("--by", required=True, metavar="NAME", help="who made the decision")
    rec.add_argument("--summary", metavar="TEXT", help="what was decided")
    rec.add_argument("--option", action="append", metavar="TEXT", help="an option (repeatable)")
    rec.add_argument("--chosen", metavar="TEXT", help="the option that was chosen")
    rec.add_argument("--reasoning", metavar="TEXT", help="why")
    rec.add_argument("--cite-node", action="append", metavar="ID", help="a graph node id")
    rec.add_argument("--cite-file", action="append", metavar="PATH", help="a file under --root")
    rec.add_argument("--cite-decision", action="append", metavar="ID", help="an earlier decision")
    rec.add_argument("--supersedes", metavar="ID", help="the earlier decision this replaces")
    rec.add_argument("--run", metavar="RUN_ID", help="the run it was made in (with --step)")
    rec.add_argument("--step", metavar="STEP_ID", help="the step it was made in (with --run)")
    rec.add_argument("--root", type=Path, metavar="DIR", help="application root")
    rec.add_argument("--app-id", metavar="ID", help="application id (default: derived from --root)")
    rec.add_argument("--from-json", type=Path, metavar="FILE", help="read the content from JSON")
    rec.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")

    link = d_sub.add_parser("link", help="link a decision to a commit, branch or pull request")
    link.add_argument("decision_id", metavar="ID")
    link.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    link.add_argument("--by", required=True, metavar="NAME", help="who states the link")
    target = link.add_mutually_exclusive_group(required=True)
    target.add_argument("--commit", metavar="SHA")
    target.add_argument("--branch", metavar="NAME")
    target.add_argument("--pr", metavar="REF", help="for example org/repo#12; not checked")
    link.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")

    lst = d_sub.add_parser("list", help="list decisions with their status")
    _add_read_args(lst)
    show = d_sub.add_parser("show", help="show one decision in full")
    show.add_argument("decision_id", metavar="ID")
    _add_read_args(show)
    trace = d_sub.add_parser(
        "trace", help="from a decision, commit, branch or PR back to its evidence and outcome"
    )
    trace.add_argument("decision_id", nargs="?", metavar="ID")
    trace.add_argument("--commit", metavar="SHA")
    trace.add_argument("--branch", metavar="NAME")
    trace.add_argument("--pr", metavar="REF")
    _add_read_args(trace)


def _clean(text: str) -> str:
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def run(args: argparse.Namespace) -> int:
    handlers = {
        "record": _record,
        "link": _link,
        "list": _list,
        "show": _show,
        "trace": _trace,
    }
    try:
        return handlers[args.decisions_command](args)
    except DecisionError as exc:
        print(_clean(str(exc)), file=sys.stderr)
        return EXIT_DECISION_ERROR
    except TraceStoreError as exc:
        print(_clean(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    except _InputError as exc:
        print(_clean(str(exc)), file=sys.stderr)
        return exc.code


class _InputError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _check_root(root: Path | None) -> None:
    if root is not None and not root.is_dir():
        raise _InputError("ingestion root does not exist", EXIT_INGESTION_ERROR)


def _content(args: argparse.Namespace) -> dict[str, Any]:
    """The record's content from flags, or from --from-json (the two are exclusive)."""
    if args.from_json is None:
        if not (args.summary and args.option and args.chosen and args.reasoning):
            raise _InputError(
                "give --summary, --option, --chosen and --reasoning, or --from-json",
                EXIT_CONFIG_ERROR,
            )
        return {
            "summary": args.summary,
            "options": list(args.option),
            "chosen": args.chosen,
            "reasoning": args.reasoning,
            "cite_nodes": list(args.cite_node or []),
            "cite_files": list(args.cite_file or []),
            "cite_decisions": list(args.cite_decision or []),
            "supersedes": args.supersedes,
            "run_id": args.run,
            "step_id": args.step,
        }
    if any(getattr(args, name) for name in _CONTENT_FLAGS):
        raise _InputError("--from-json cannot be combined with content flags", EXIT_CONFIG_ERROR)
    try:
        if args.from_json.stat().st_size > MAX_JSON_BYTES:
            raise _InputError("the JSON file is too large", EXIT_CONFIG_ERROR)
        data = json.loads(args.from_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _InputError("cannot read the JSON file", EXIT_CONFIG_ERROR) from None
    allowed = {
        "summary",
        "options",
        "chosen",
        "reasoning",
        "cite_nodes",
        "cite_files",
        "cite_decisions",
        "supersedes",
        "run_id",
        "step_id",
    }
    if not isinstance(data, dict) or not set(data) <= allowed:
        raise _InputError("the JSON file is not a decision (unknown or missing fields)", 2)
    missing = [k for k in ("summary", "options", "chosen", "reasoning") if k not in data]
    lists = ("options", "cite_nodes", "cite_files", "cite_decisions")
    if missing or any(
        k in data and not (isinstance(data[k], list) and all(isinstance(i, str) for i in data[k]))
        for k in lists
    ):
        raise _InputError("the JSON file is not a decision (unknown or missing fields)", 2)
    return data


def _record(args: argparse.Namespace) -> int:
    _check_root(args.root)
    content = _content(args)
    with TraceStore.create(args.db) as store:
        decision, links = record_decision(
            store,
            made_by=MadeBy.PERSON,
            recorder=args.by,
            root=args.root,
            app_id=args.app_id,
            **content,
        )
    if args.as_json:
        payload = decision_payload(decision, None, links)
        print(json.dumps(payload, indent=2))
        return EXIT_OK
    print(f"recorded {decision.decision_id}")
    app = _clean(decision.app_id or "unrecorded")
    print(f"  by {_clean(args.by)} (person-written); application {app}")
    print(f"  code version {decision.fingerprint[:12] if decision.fingerprint else 'unrecorded'}")
    for link in links:
        print(f"  observed {link.kind.value} {_clean(link.value)}")
    if args.root is not None and not links:
        print("  no git commit or branch was observed for the root")
    return EXIT_OK


def _link(args: argparse.Namespace) -> int:
    if not DECISION_ID.fullmatch(args.decision_id):
        raise DecisionError("the decision id is malformed")
    kind, value = (
        (LinkKind.COMMIT, args.commit)
        if args.commit is not None
        else (LinkKind.BRANCH, args.branch)
        if args.branch is not None
        else (LinkKind.PR, args.pr)
    )
    link = DecisionLink(
        decision_id=args.decision_id,
        kind=kind,
        value=value,
        how=LinkHow.DECLARED,
        recorder=args.by,
        created_at=utc_now(),
    )
    with TraceStore.create(args.db) as store:
        store.add_decision_link(link)
    if args.as_json:
        print(json.dumps(link.model_dump(mode="json"), indent=2))
    else:
        print(f"linked {args.decision_id} to {kind.value} {_clean(value)} (declared, not checked)")
    return EXIT_OK


def _view(args: argparse.Namespace, store: TraceStore) -> DecisionsView:
    from cognitive_mesh.experience import ExperienceGraph, ExperienceGraphError
    from cognitive_mesh.experience import store as graph_store
    from cognitive_mesh.experience.outcomes import OutcomeStore, OutcomeStoreError
    from cognitive_mesh.experience.reviews import ReviewStore, ReviewStoreError

    _check_root(args.root)
    try:
        graph = ExperienceGraph(graph_store.load(args.graph).snapshot) if args.graph else None
    except ExperienceGraphError as exc:
        raise _InputError(str(exc), EXIT_EXPERIENCE_ERROR) from None
    try:
        reviews = None
        if args.reviews is not None:
            with ReviewStore.open_existing(args.reviews) as reviews_store:
                reviews = reviews_store.list_reviews()
    except ReviewStoreError as exc:
        raise _InputError(str(exc), EXIT_REVIEW_STORE_ERROR) from None
    try:
        outcomes = None
        if args.outcomes is not None:
            with OutcomeStore.open_existing(args.outcomes) as outcomes_store:
                outcomes = outcomes_store.list_outcomes()
    except OutcomeStoreError as exc:
        raise _InputError(str(exc), EXIT_OUTCOME_STORE_ERROR) from None
    from cognitive_mesh.validity import utc_today

    return assess_all(
        store, graph=graph, reviews=reviews, outcomes=outcomes, root=args.root, today=utc_today()
    )


def _print_header(view: DecisionsView) -> None:
    if not view.root_given:
        print("no --root given: cited files and recorded outcomes cannot be matched to the code")
    if not view.graph_given:
        print("no --graph given: cited graph nodes cannot be checked")
    if view.listing.malformed:
        ids = ", ".join(_clean(i) for i in view.listing.malformed)
        print(f"{len(view.listing.malformed)} unreadable decision row(s): {ids}", file=sys.stderr)


def _list(args: argparse.Namespace) -> int:
    with TraceStore.open_existing(args.db) as store:
        view = _view(args, store)
    if args.as_json:
        rows = [
            decision_payload(d, view.assessments.get(d.decision_id), _links_of(view, d))
            for d in view.listing.decisions
        ]
        print(
            json.dumps(
                {"decisions": rows, "malformed": list(view.listing.malformed), "note": NOTE},
                indent=2,
            )
        )
        return EXIT_OK
    _print_header(view)
    for d in view.listing.decisions:
        a = view.assessments[d.decision_id]
        print(f"[{a.status.value}] {d.decision_id}  {_clean(d.summary)}")
        print(f"    {d.basis.value}, {d.created_at.date().isoformat()}, chose: {_clean(d.chosen)}")
    if not view.listing.decisions:
        print("no decisions recorded")
    print(NOTE)
    return EXIT_OK


def _links_of(view: DecisionsView, d: Decision) -> list[DecisionLink]:
    return [link for link in view.listing.links if link.decision_id == d.decision_id]


def _find(view: DecisionsView, decision_id: str) -> Decision:
    for d in view.listing.decisions:
        if d.decision_id == decision_id:
            return d
    if decision_id in view.listing.malformed:
        raise _InputError("the decision row is unreadable", EXIT_DECISION_ERROR)
    raise _InputError("no decision has this id", EXIT_NOT_FOUND)


def _print_decision(view: DecisionsView, d: Decision, a: DecisionAssessment) -> None:
    print(f"[{a.status.value}] {d.decision_id}")
    print(f"  {a.meaning}")
    print(f"  summary: {_clean(d.summary)}")
    who = (
        f"{_clean(d.recorder or '')} (person)"
        if d.made_by is MadeBy.PERSON
        else f"{_clean(d.provider or '')}/{_clean(d.model or '')} as {_clean(d.role or '')} "
        "(model-written, unverified)"
    )
    print(f"  by {who} on {d.created_at.isoformat()}")
    print(f"  application {_clean(d.app_id or 'unrecorded')}; code version at the time ", end="")
    print(d.fingerprint[:12] if d.fingerprint else "unrecorded")
    if d.run_id is not None:
        print(f"  made in run {_clean(d.run_id)} step {_clean(d.step_id or '')}")
    print("  options: " + " | ".join(_clean(o) for o in d.options))
    print(f"  chosen: {_clean(d.chosen)}")
    print(f"  reasoning ({d.basis.value}, data, not evidence):")
    for line in d.reasoning.splitlines() or [""]:
        print(f"    {_clean(line)}")
    if a.citations:
        print("  evidence cited:")
    for c in a.citations:
        print(f"    [{c.state.value}] {c.kind.value} {_clean(c.target)}: {c.reason}")
    if d.supersedes:
        print(f"  replaces {d.supersedes}")
    for later in a.superseded_by:
        print(f"  replaced by {later}")
    for other in a.competing:
        print(f"  competes with {other} (replaces the same earlier decision)")
    print(f"  outcome for the current code version: {a.outcome.value}")
    for o in a.outcomes:
        print(
            f"    outcome #{o.id} {o.source.value} {o.result.value} on {o.fingerprint[:12]} "
            f"by {_clean(o.recorder)}"
        )
    for link in _links_of(view, d):
        by = _clean(link.recorder)
        print(f"  {link.how.value} {link.kind.value} {_clean(link.value)} (by {by})")
    for reason in a.reasons:
        print(f"  note: {_clean(reason)}")


def _show(args: argparse.Namespace) -> int:
    with TraceStore.open_existing(args.db) as store:
        view = _view(args, store)
    d = _find(view, args.decision_id)
    a = view.assessments[d.decision_id]
    if args.as_json:
        print(json.dumps({**decision_payload(d, a, _links_of(view, d)), "note": NOTE}, indent=2))
        return EXIT_OK
    _print_header(view)
    _print_decision(view, d, a)
    print(NOTE)
    return EXIT_OK


def _trace(args: argparse.Namespace) -> int:
    chosen = [x for x in (args.decision_id, args.commit, args.branch, args.pr) if x]
    if len(chosen) != 1:
        raise _InputError("give exactly one of ID, --commit, --branch or --pr", EXIT_CONFIG_ERROR)
    with TraceStore.open_existing(args.db) as store:
        view = _view(args, store)
    if args.decision_id:
        roots = [_find(view, args.decision_id)]
    else:
        kind = LinkKind.COMMIT if args.commit else LinkKind.BRANCH if args.branch else LinkKind.PR
        roots = matching_links(view.listing, kind, chosen[0])
        if not roots:
            raise _InputError("no decision is linked to this reference", EXIT_NOT_FOUND)
    shown: dict[str, Decision] = {}
    for d in roots:
        for related in history_of(view.listing, d.decision_id):
            shown.setdefault(related.decision_id, related)
    root_ids = {d.decision_id for d in roots}
    ordered = sorted(
        shown.values(), key=lambda x: (x.decision_id not in root_ids, x.created_at, x.decision_id)
    )
    if args.as_json:
        rows = [
            decision_payload(d, view.assessments[d.decision_id], _links_of(view, d))
            for d in ordered
        ]
        print(
            json.dumps(
                {"roots": [d.decision_id for d in roots], "decisions": rows, "note": NOTE},
                indent=2,
            )
        )
        return EXIT_OK
    _print_header(view)
    for d in ordered:
        print("--- " + ("asked for" if d.decision_id in root_ids else "related (supersession)"))
        _print_decision(view, d, view.assessments[d.decision_id])
    print(NOTE)
    return EXIT_OK
