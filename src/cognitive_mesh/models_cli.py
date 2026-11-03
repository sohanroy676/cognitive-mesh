"""The `models` commands (ADR-0038): list the catalog, summarise recorded model calls.

`models list` makes no network call and never prints a key. `models stats` is read-only.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from cognitive_mesh.config import ConfigError
from cognitive_mesh.model_stats import GroupStats, ModelStats
from cognitive_mesh.providers.catalog import Catalog, CatalogError, load_catalog
from cognitive_mesh.providers.factory import read_key
from cognitive_mesh.traces import TraceStore, TraceStoreError

EXIT_OK = 0
EXIT_TRACE_STORE_ERROR = 8  # same code as cli.EXIT_TRACE_STORE_ERROR (a test pins this)
EXIT_CATALOG_ERROR = 20


def add_parser(sub: Any) -> None:
    models = sub.add_parser("models", help="the model catalog and recorded model calls")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    listing = models_sub.add_parser("list", help="list catalog entries (no network call)")
    listing.add_argument(
        "--catalog", type=Path, metavar="FILE", help="catalog file (default: COGMESH_MODEL_CATALOG)"
    )
    listing.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    stats = models_sub.add_parser(
        "stats", help="summarise recorded model calls per model, role and task type"
    )
    stats.add_argument("--db", type=Path, required=True, metavar="FILE", help="trace database")
    stats.add_argument("--task-type", metavar="T", help="only calls made for this capability")
    stats.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")


def run(args: argparse.Namespace) -> int:
    if args.models_command == "list":
        return _list(args.catalog, args.as_json)
    return _stats(args.db, args.task_type, args.as_json)


def _clean(text: str) -> str:
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _catalog_path(given: Path | None) -> Path | None:
    if given is not None:
        return given
    value = os.environ.get("COGMESH_MODEL_CATALOG") or dotenv_values(".env").get(
        "COGMESH_MODEL_CATALOG"
    )
    return Path(value) if value else None


def _key_state(variable: str | None) -> str:
    if variable is None:
        return "none needed"
    try:
        read_key(variable)
    except ConfigError:
        return "not set (or not valid)"
    return "set"


def _list(catalog_arg: Path | None, as_json: bool) -> int:
    path = _catalog_path(catalog_arg)
    if path is None:
        print("no catalog: pass --catalog FILE or set COGMESH_MODEL_CATALOG", file=sys.stderr)
        return EXIT_CATALOG_ERROR
    try:
        catalog = load_catalog(path)
    except CatalogError as exc:
        print(f"model catalog: {_clean(str(exc))}", file=sys.stderr)
        return EXIT_CATALOG_ERROR
    shared = catalog.key_env_hosts()
    if as_json:
        print(json.dumps(_catalog_json(catalog, shared), indent=2))
        return EXIT_OK
    print(
        f"{len(catalog.models)} catalog entries (data_use and tier are as declared, not verified)"
    )
    for entry_id, entry in sorted(catalog.models.items()):
        where = "remote" if entry.remote else "local"
        print(f"{entry_id}: {entry.wire} {_clean(entry.base_url)} model {_clean(entry.model)}")
        print(
            f"    {where} host {_clean(entry.host)}; tier {entry.tier}; data_use {entry.data_use}; "
            f"context {entry.context_tokens} tokens"
        )
        print(f"    roles: {', '.join(entry.roles)}")
        print(f"    key variable: {entry.api_key_env or '-'} ({_key_state(entry.api_key_env)})")
    for variable, hosts in shared.items():
        print(
            f"warning: {variable} is used by entries on different hosts "
            f"({', '.join(_clean(h) for h in hosts)}); the same key would be sent to each",
            file=sys.stderr,
        )
    return EXIT_OK


def _catalog_json(catalog: Catalog, shared: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    entries = []
    for entry_id, entry in sorted(catalog.models.items()):
        item = entry.model_dump(mode="json")
        item.update(
            id=entry_id,
            host=entry.host,
            remote=entry.remote,
            key_state=_key_state(entry.api_key_env),
        )
        entries.append(item)
    return {
        "entries": entries,
        "key_variables_on_several_hosts": {k: list(v) for k, v in shared.items()},
    }


def _stats(db: Path, task_type: str | None, as_json: bool) -> int:
    try:
        with TraceStore.open_existing(db) as store:
            listing = store.call_rows()
    except TraceStoreError as exc:
        print(_clean(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    stats = ModelStats.from_listing(listing, task_type)
    if stats.malformed_runs:
        print(
            "warning: unreadable call records in run(s): " + ", ".join(stats.malformed_runs),
            file=sys.stderr,
        )
    if as_json:
        print(stats.model_dump_json(indent=2))
        return EXIT_OK
    print(
        f"{stats.runs_total} run(s) in the database, {stats.runs_with_calls} with model call "
        "records (earlier runs have none)"
    )
    if task_type is not None:
        print(f"only task type: {_clean(task_type)}")
    if not stats.groups:
        print("no recorded model calls")
    for group in stats.groups:
        _print_group(group)
    print(stats.note)
    return EXIT_OK


def _print_group(g: GroupStats) -> None:
    source = f"entry {g.entry_id}, " if g.entry_id else ""
    print(
        f"{source}{_clean(g.provider)}/{_clean(g.model)}  role {_clean(g.role)}  "
        f"task {_clean(g.task_type)}"
    )
    results = ", ".join(f"{name} {n}" for name, n in g.results.items())
    print(f"    calls: {g.calls} ({results})")
    if g.median_latency_ms is None:
        print("    latency of ok calls: none recorded")
    else:
        print(
            f"    latency of ok calls: median {g.median_latency_ms} ms, "
            f"p90 {g.p90_latency_ms} ms (n={g.ok_latency_n})"
        )
    for label, tokens in (("prompt", g.prompt_tokens), ("completion", g.completion_tokens)):
        print(
            f"    {label} tokens: {tokens.total} over {tokens.known_calls} ok call(s) that "
            f"reported them; unknown for {tokens.unknown_calls}"
        )
    print(
        f"    attempts: success {g.attempts_success}, failure {g.attempts_failure}, "
        f"no matching attempt {g.attempts_unmatched}"
    )
