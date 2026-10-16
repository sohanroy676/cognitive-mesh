"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cognitive_mesh import __version__
from cognitive_mesh.config import ConfigError, load_settings
from cognitive_mesh.graph import DependencyGraph, GraphError, build_dependency_graph
from cognitive_mesh.ingestion import IngestionError, IngestionResult, ingest_application

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INGESTION_ERROR = 3
EXIT_GRAPH_ERROR = 4


def _printable(text: str) -> str:
    """Escape control characters so untrusted file names cannot drive the terminal."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cognitive-mesh")
    sub = parser.add_subparsers(dest="command")
    ingest = sub.add_parser("ingest", help="inspect what ingestion discovers in a directory")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    ingest.add_argument(
        "--include-content", action="store_true", help="with --json, include file contents"
    )
    graph = sub.add_parser("graph", help="build the dependency graph of a directory")
    graph.add_argument("path", type=Path)
    graph.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    graph.add_argument("--deps-of", metavar="NODE_ID", help="list what NODE_ID depends on")
    graph.add_argument("--transitive", action="store_true", help="with --deps-of, follow indirect")
    return parser


def _print_ingestion(result: IngestionResult) -> None:
    print(f"ingested {_printable(result.root)}: {len(result.files)} files")
    for file in result.files:
        print(f"  {file.kind.value:<13} {_printable(file.ref.path)}  ({file.matched_rule})")
    if result.diagnostics:
        print(f"diagnostics: {len(result.diagnostics)}")
        for diag in result.diagnostics:
            print(f"  [{diag.severity}] {diag.code.value} {_printable(diag.path)}: {diag.message}")


def _run_ingest(path: Path, as_json: bool, include_content: bool) -> int:
    try:
        result = ingest_application(path)
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    if as_json:
        exclude = None if include_content else {"files": {"__all__": {"content"}}}
        payload = result.model_dump(mode="json", exclude=exclude)
        payload["summary"] = result.summary()
        print(json.dumps(payload, indent=2))
    else:
        _print_ingestion(result)
    return EXIT_OK


def _print_graph(graph: DependencyGraph) -> None:
    summary = graph.summary()
    print(f"nodes: {summary['nodes_by_kind']}")
    print(f"edges: {summary['edges_by_kind']}")
    for node in graph.unresolved():
        print(f"  unresolved: {_printable(node.id)}")
    cycles, truncated = graph.cycles()
    for cycle in cycles:
        print("  cycle: " + " -> ".join(_printable(c) for c in (*cycle, cycle[0])))
    if truncated:
        print("  cycles truncated: more exist than were listed")
    print(f"diagnostics: {len(graph.diagnostics)}")
    for diag in graph.diagnostics:
        print(f"  [{diag.severity}] {diag.code.value} {_printable(diag.path)}: {diag.message}")


def _print_deps(graph: DependencyGraph, node_id: str, transitive: bool) -> None:
    if transitive:
        for reached in graph.transitive_dependencies(node_id):
            via = reached.path[-1].evidence[0]
            print(
                f"  d{reached.depth} {_printable(reached.node.id)}  "
                f"({_printable(via.ref.path)}:{via.line}, {via.rule})"
            )
    else:
        for edge in graph.direct_dependencies(node_id):
            via = edge.evidence[0]
            print(
                f"  {edge.kind.value} {_printable(edge.target)}  "
                f"({_printable(via.ref.path)}:{via.line}, {via.rule})"
            )


def _run_graph(path: Path, as_json: bool, deps_of: str | None, transitive: bool) -> int:
    try:
        graph = build_dependency_graph(ingest_application(path))
        if deps_of is not None:
            graph.node(deps_of)  # fail before printing anything
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    except GraphError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_GRAPH_ERROR
    if as_json:
        print(json.dumps(graph.to_dict(), indent=2))
    elif deps_of is not None:
        print(f"dependencies of {_printable(deps_of)}:")
        _print_deps(graph, deps_of, transitive)
    else:
        _print_graph(graph)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """With no subcommand, validate configuration. `ingest` and `graph` need no provider config.

    Neither path makes provider network calls.
    """
    args = _build_parser().parse_args(argv)
    if args.command == "ingest":
        return _run_ingest(args.path, args.as_json, args.include_content)
    if args.command == "graph":
        return _run_graph(args.path, args.as_json, args.deps_of, args.transitive)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"cognitive-mesh {__version__}: config OK (provider={settings.provider})")
    return EXIT_OK
