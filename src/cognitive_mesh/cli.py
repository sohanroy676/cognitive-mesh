"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cognitive_mesh import __version__
from cognitive_mesh.agents import default_registry
from cognitive_mesh.config import ConfigError, load_settings
from cognitive_mesh.graph import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    DependencyGraph,
    GraphError,
    build_dependency_graph,
    build_view,
    render_html,
    render_mermaid,
)
from cognitive_mesh.ingestion import IngestionError, IngestionResult, ingest_application

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INGESTION_ERROR = 3
EXIT_GRAPH_ERROR = 4
EXIT_OUTPUT_ERROR = 5


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
    graph.add_argument(
        "--format", choices=["mermaid", "html"], dest="fmt", help="render a diagram instead"
    )
    graph.add_argument("--output", type=Path, metavar="FILE", help="with --format, write to FILE")
    graph.add_argument("--max-nodes", type=_positive_int, help="with --format, node limit")
    graph.add_argument("--max-edges", type=_positive_int, help="with --format, edge limit")
    agents = sub.add_parser("agents", help="list agent roles and their declared capabilities")
    agents.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    return parser


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {_printable(text)!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


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


def _run_graph_view(
    path: Path, fmt: str, output: Path | None, max_nodes: int | None, max_edges: int | None
) -> int:
    try:
        graph = build_dependency_graph(ingest_application(path))
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    view = build_view(
        graph, max_nodes or DEFAULT_MAX_NODES[fmt], max_edges or DEFAULT_MAX_EDGES[fmt]
    )
    text = render_html(view) if fmt == "html" else render_mermaid(view)
    if view.truncation.truncated:
        print(view.truncation.notice(), file=sys.stderr)
    if output is None:
        print(text, end="")
        return EXIT_OK
    try:
        output.write_text(text, encoding="ascii", newline="\n")
    except OSError as exc:
        print(_printable(f"cannot write output: {exc}"), file=sys.stderr)
        return EXIT_OUTPUT_ERROR
    print(f"wrote {fmt} graph to {_printable(str(output))}")
    return EXIT_OK


def _run_agents(as_json: bool) -> int:
    specs = default_registry().specs()
    if as_json:
        print(json.dumps({"agents": [s.model_dump(mode="json") for s in specs]}, indent=2))
        return EXIT_OK
    for spec in specs:
        print(f"{spec.id}  ({spec.role})")
        print(f"  {_printable(spec.description)}")
        for cap in spec.capabilities:
            kind = "uses model" if cap.uses_model else "deterministic"
            print(f"  - {cap.name} [{kind}]: {_printable(cap.description)}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """With no subcommand, validate configuration. `ingest`, `graph` and `agents` need no config.

    None of these paths makes provider network calls.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _run_ingest(args.path, args.as_json, args.include_content)
    if args.command == "agents":
        return _run_agents(args.as_json)
    if args.command == "graph":
        if args.fmt is not None:
            if args.as_json or args.deps_of is not None or args.transitive:
                parser.error("--format cannot be combined with --json, --deps-of or --transitive")
            return _run_graph_view(args.path, args.fmt, args.output, args.max_nodes, args.max_edges)
        if args.output is not None or args.max_nodes is not None or args.max_edges is not None:
            parser.error("--output, --max-nodes and --max-edges require --format")
        return _run_graph(args.path, args.as_json, args.deps_of, args.transitive)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"cognitive-mesh {__version__}: config OK (provider={settings.provider})")
    return EXIT_OK
