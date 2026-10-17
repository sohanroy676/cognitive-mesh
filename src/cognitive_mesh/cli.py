"""Command-line entry point."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from cognitive_mesh import __version__
from cognitive_mesh.agents import AgentContext, AgentRegistry, default_registry
from cognitive_mesh.config import ConfigError, load_settings, load_settings_if_configured
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
from cognitive_mesh.orchestrator import (
    Orchestrator,
    PlanError,
    PlanGenerationError,
    TaskPlan,
    TaskRun,
    TaskStatus,
    ValidatedPlan,
    generate_plan,
    plan_task,
)
from cognitive_mesh.providers import build_model_client
from cognitive_mesh.traces import (
    RunTrace,
    TraceNotFoundError,
    TraceRecorder,
    TraceStatus,
    TraceStore,
    TraceStoreError,
    utc_now,
)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_INGESTION_ERROR = 3
EXIT_GRAPH_ERROR = 4
EXIT_OUTPUT_ERROR = 5
EXIT_PLAN_ERROR = 6
EXIT_TASK_NOT_SUCCEEDED = 7
EXIT_TRACE_STORE_ERROR = 8
EXIT_TRACE_NOT_FOUND = 9
EXIT_PLAN_GENERATION_ERROR = 10
MAX_PLAN_BYTES = 256 * 1024


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
    run = sub.add_parser("run", help="run a task plan (JSON) against a directory")
    run.add_argument("plan", type=Path, help="path to the plan JSON file")
    run.add_argument("--root", type=Path, required=True, help="application directory to analyze")
    run.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    run.add_argument(
        "--trace-db", type=Path, metavar="FILE", help="record the run in this SQLite trace database"
    )
    plan = sub.add_parser(
        "plan", help="draft a plan (JSON) from a request with the configured model; runs nothing"
    )
    plan.add_argument("request", help="what you want done, in plain words")
    plan.add_argument("--task-id", default="generated", help="task id for the plan")
    plan.add_argument("--output", type=Path, metavar="FILE", help="write the plan to FILE")
    traces = sub.add_parser("traces", help="read recorded run traces")
    traces_sub = traces.add_subparsers(dest="traces_command", required=True)
    traces_list = traces_sub.add_parser("list", help="list recorded runs")
    traces_list.add_argument("--db", type=Path, required=True, metavar="FILE")
    traces_list.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    traces_show = traces_sub.add_parser("show", help="show one recorded run")
    traces_show.add_argument("run_id")
    traces_show.add_argument("--db", type=Path, required=True, metavar="FILE")
    traces_show.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
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


def _load_plan(path: Path) -> TaskPlan:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_PLAN_BYTES + 1)
    except OSError:
        raise PlanError("cannot read the plan file") from None
    if len(data) > MAX_PLAN_BYTES:
        raise PlanError(f"the plan file is larger than {MAX_PLAN_BYTES} bytes")
    try:
        return TaskPlan.model_validate_json(data, strict=True)
    except ValidationError as exc:
        kinds = ", ".join(sorted({str(e["type"]) for e in exc.errors()}))
        raise PlanError(f"the plan file is not a valid plan ({kinds})") from None


def _run_plan_request(request: str, task_id: str, output: Path | None) -> int:
    """Draft a plan with the configured model. The plan is printed or written, never executed."""
    try:
        settings = load_settings()
        client = build_model_client(settings)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        plan, response = generate_plan(
            request,
            default_registry(),
            client,
            task_id=task_id,
            timeout_s=settings.model_timeout_s,
        )
    except PlanGenerationError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_PLAN_GENERATION_ERROR
    text = plan.model_dump_json(indent=2)
    if output is None:
        print(text)
    else:
        try:
            output.write_text(text + "\n", encoding="utf-8", newline="\n")
        except OSError:
            print("cannot write the plan file", file=sys.stderr)
            return EXIT_OUTPUT_ERROR
    print(
        f"drafted by {_printable(response.provider)}/{_printable(response.model)}: "
        "model-written and unreviewed; read it before running it with `run`",
        file=sys.stderr,
    )
    return EXIT_OK


def _print_run(run: TaskRun) -> None:
    print(f"task {run.task_id}: {run.status.value} ({run.total_attempts} attempts)")
    for step in run.steps:
        line = f"  {step.step_id:<20} {step.status.value:<9} {step.capability}"
        if step.agent_id:
            line += f"  agent={step.agent_id}"
        if step.attempt_count > 1:
            line += f"  attempts={step.attempt_count}"
        print(line)
        if step.blocked_by:
            print(f"    blocked by: {', '.join(step.blocked_by)}")
        if step.failure_kind:
            print(f"    failure: {step.failure_kind}: {_printable(step.message or '')}")


_NO_TIMES = {"steps": {"__all__": {"started_at", "finished_at"}}}


def _emit_run(run: TaskRun, as_json: bool, trace_id: str | None) -> None:
    if as_json:
        payload = run.model_dump(mode="json", exclude=None if trace_id else _NO_TIMES)
        if trace_id:
            payload["trace_id"] = trace_id
        print(json.dumps(payload, indent=2))
        return
    _print_run(run)
    if trace_id:
        print(f"trace: {trace_id}")


def _registry_for(validated: ValidatedPlan) -> AgentRegistry:
    """Config is read only when the plan has a model step; no provider set means no client."""
    registry = default_registry()
    model_caps = {c.name for spec in registry.specs() for c in spec.capabilities if c.uses_model}
    if not any(step.capability in model_caps for step in validated.order):
        return registry
    settings = load_settings_if_configured()
    if settings is None:
        return registry
    return default_registry(build_model_client(settings), model_timeout_s=settings.model_timeout_s)


def _run_plan(plan_path: Path, root: Path, as_json: bool, trace_db: Path | None) -> int:
    registry = default_registry()
    try:
        validated = plan_task(_load_plan(plan_path), registry)
        ingestion = ingest_application(root)
        registry = _registry_for(validated)
    except PlanError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_PLAN_ERROR
    except IngestionError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_INGESTION_ERROR
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    context = AgentContext(ingestion=ingestion, graph=build_dependency_graph(ingestion))
    if trace_db is None:
        run = Orchestrator(registry).run(validated, context)
        _emit_run(run, as_json, None)
        return EXIT_OK if run.status is TaskStatus.SUCCEEDED else EXIT_TASK_NOT_SUCCEEDED
    try:
        store = TraceStore.create(trace_db)
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    with store:
        recorder = TraceRecorder(store)
        try:
            run_id = recorder.begin(validated, ingestion)
        except TraceStoreError as exc:  # nothing ran: the user asked for a trace we cannot keep
            print(_printable(str(exc)), file=sys.stderr)
            return EXIT_TRACE_STORE_ERROR
        run = Orchestrator(registry, now=utc_now).run(validated, context)
        try:
            recorder.finish(run_id, run)
        except TraceStoreError as exc:
            _emit_run(run, as_json, None)
            print(f"warning: the trace was not saved: {_printable(str(exc))}", file=sys.stderr)
            return EXIT_TRACE_STORE_ERROR
    _emit_run(run, as_json, run_id)
    return EXIT_OK if run.status is TaskStatus.SUCCEEDED else EXIT_TASK_NOT_SUCCEEDED


def _print_trace(trace: RunTrace) -> None:
    print(f"run {_printable(trace.run_id)}: {trace.status.value}  task={_printable(trace.task_id)}")
    finished = f"  finished {trace.finished_at.isoformat()}" if trace.finished_at else ""
    print(f"  started {trace.started_at.isoformat()}{finished}")
    if trace.source_root is not None:
        print(
            f"  source: {_printable(trace.source_root)}  "
            f"fingerprint={_printable(trace.source_fingerprint or '')}"
        )
    env = trace.environment
    print(
        f"  environment: cognitive-mesh {_printable(env.cognitive_mesh_version)}, "
        f"python {_printable(env.python_version)}, {_printable(env.platform)}"
    )
    if trace.redactions:
        print("  redacted: " + ", ".join(_printable(r) for r in trace.redactions))
    if trace.status is TraceStatus.INTERRUPTED:
        print("  the run never finished; no step data was stored")
    for step in trace.steps:
        print(
            f"  {_printable(step.step_id):<20} {step.status.value:<9} {_printable(step.capability)}"
        )
        if step.agent_id:
            print(f"    agent={_printable(step.agent_id)} attempts={step.attempt_count}")
        if step.blocked_by:
            print(f"    blocked by: {', '.join(_printable(b) for b in step.blocked_by)}")
        if step.failure_kind:
            print(f"    failure: {_printable(step.failure_kind)}: {_printable(step.message or '')}")
        for attempt in step.attempts:
            result = attempt.result
            detail = (
                f"success basis={result.basis.value}"
                if result.status == "success"
                else f"failure {result.kind.value}"
            )
            print(
                f"    attempt {attempt.attempt_no} ({_printable(attempt.agent_task_id)}): {detail}"
            )


def _run_traces_list(db: Path, as_json: bool) -> int:
    try:
        with TraceStore.open_existing(db) as store:
            listing = store.list()
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    if as_json:
        print(json.dumps(listing.model_dump(mode="json"), indent=2))
    else:
        for r in listing.runs:
            print(
                f"{_printable(r.run_id)}  {r.status.value:<11} {r.started_at.isoformat()}  "
                f"task={_printable(r.task_id)}  steps={r.step_count}"
            )
        if not listing.runs and not listing.malformed:
            print("no runs recorded")
    if listing.malformed:
        ids = ", ".join(_printable(i) for i in listing.malformed)
        print(f"{len(listing.malformed)} malformed run(s) not shown: {ids}", file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    return EXIT_OK


def _run_traces_show(run_id: str, db: Path, as_json: bool) -> int:
    try:
        with TraceStore.open_existing(db) as store:
            trace = store.get(run_id)
    except TraceNotFoundError:
        print(f"no run has the id {_printable(run_id)!r}", file=sys.stderr)
        return EXIT_TRACE_NOT_FOUND
    except TraceStoreError as exc:
        print(_printable(str(exc)), file=sys.stderr)
        return EXIT_TRACE_STORE_ERROR
    if as_json:
        print(json.dumps(trace.model_dump(mode="json"), indent=2))
    else:
        _print_trace(trace)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """With no subcommand, validate configuration. Other subcommands need no config.

    Only `run` can call a model, and only when the plan has a model step and a provider is set.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _run_ingest(args.path, args.as_json, args.include_content)
    if args.command == "run":
        return _run_plan(args.plan, args.root, args.as_json, args.trace_db)
    if args.command == "plan":
        return _run_plan_request(args.request, args.task_id, args.output)
    if args.command == "traces":
        if args.traces_command == "list":
            return _run_traces_list(args.db, args.as_json)
        return _run_traces_show(args.run_id, args.db, args.as_json)
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
