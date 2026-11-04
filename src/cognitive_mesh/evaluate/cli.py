"""The `eval` commands (ADR-0043): run a task set with memory off and on, and report the counts.

`eval run` makes real changes on new branches in separate worktrees and, with
`--allow-unisolated-checks`, runs each task's check command with your privileges (not isolated).
Nothing is pushed. `eval report` reads the stores and says plainly when the sample is too small.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cognitive_mesh.change.cli import registry_factory_for
from cognitive_mesh.config import ConfigError, load_settings
from cognitive_mesh.evaluate.errors import EvalError
from cognitive_mesh.evaluate.report import build_report, render_text
from cognitive_mesh.evaluate.runner import MAX_REPEATS, EvalSettings, run_evaluation
from cognitive_mesh.evaluate.taskset import load_taskset
from cognitive_mesh.providers import CatalogError, catalog_entry_id

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_EVAL_ERROR = 25


def add_parser(sub: Any) -> None:
    ev = sub.add_parser("eval", help="compare memory off and on for small changes (ADR-0043)")
    es = ev.add_subparsers(dest="eval_command", required=True)
    run = es.add_parser("run", help="run a task set in both arms and store the records")
    run.add_argument("taskset", type=Path, help="the task set file (TOML)")
    run.add_argument("--out", type=Path, required=True, help="a new or empty output folder")
    run.add_argument("--model", required=True, metavar="ID", help="one model catalog entry")
    run.add_argument("--catalog", type=Path, required=True, metavar="FILE")
    run.add_argument("--repeats", type=int, default=1, help=f"1 to {MAX_REPEATS} (default 1)")
    run.add_argument(
        "--allow-unisolated-checks",
        action="store_true",
        help="run each task's check command; it runs with your privileges and is NOT isolated",
    )
    report = es.add_parser("report", help="print the counts of a finished or stopped evaluation")
    report.add_argument("out", type=Path, help="the output folder of `eval run`")
    report.add_argument("--eval-id", type=int, default=None)
    report.add_argument("--json", action="store_true", dest="as_json")


def run(args: argparse.Namespace) -> int:
    try:
        if args.eval_command == "run":
            return _run(args)
        return _report(args)
    except EvalError as exc:
        print(f"eval: {_plain(str(exc))}", file=sys.stderr)
        return EXIT_EVAL_ERROR
    except (ConfigError, CatalogError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR


def _plain(text: str) -> str:
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _make_embedder() -> Any:
    from cognitive_mesh.memory import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()


def _run(args: argparse.Namespace) -> int:
    ts = load_taskset(args.taskset)
    settings = load_settings(provider="catalog", model=args.model, model_catalog=args.catalog)
    entry = catalog_entry_id(settings)
    if entry is None:
        raise EvalError("the model must be a catalog entry")
    try:
        catalog_hash = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    except OSError:
        raise EvalError("cannot read the catalog file") from None
    factory = registry_factory_for(args.model, args.catalog)
    if args.allow_unisolated_checks:
        print(
            "note: each task's check command will run with your privileges and is not isolated",
            file=sys.stderr,
        )
    eval_id = run_evaluation(
        ts,
        EvalSettings(
            out=args.out,
            repeats=args.repeats,
            model_entry=entry,
            catalog_sha256=catalog_hash,
            allow_unisolated_checks=args.allow_unisolated_checks,
        ),
        registry_factory=factory,
        embedder_factory=_make_embedder,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    print(f"evaluation {eval_id} finished; records are in {_plain(str(args.out))}")
    print("nothing was pushed; run `eval report` for the counts")
    return EXIT_OK


def _report(args: argparse.Namespace) -> int:
    result = build_report(args.out, args.eval_id)
    if args.as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        print(render_text(result))
    return EXIT_OK
